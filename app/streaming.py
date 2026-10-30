"""One-time collection plus Jetstream discovery and new-user profile lookups."""
from collections import OrderedDict
import json
import logging
from typing import Callable
from time import sleep, time
from urllib.parse import urlencode

from websockets.sync.client import connect
from websockets.exceptions import WebSocketException

from app.platforms.base import SocialPlatform
from app.schemas import ActivityBatch, Content, Interaction, User

logger = logging.getLogger(__name__)


def print_event(event: dict) -> None:
    print(json.dumps(event, ensure_ascii=False), flush=True)


class StreamingWorker:
    """Fetch initial seeds once, then use Jetstream for all further discovery."""

    def __init__(self, platform: SocialPlatform, seeds: list[str], *,
                 max_users: int = 100,
                 dedup_capacity: int = 10000,
                 emit: Callable[[dict], None] = print_event):
        if not seeds or max_users < 1 or dedup_capacity < 1:
            raise ValueError('Seeds and positive capacity limits are required')
        resolved = {u.user_id: u for u in map(platform.resolve_user, seeds)}
        if len(resolved) > max_users:
            raise ValueError('max_users must accommodate all distinct seeds')
        self.platform = platform
        self.max_users = max_users
        self.dedup_capacity, self.emit = dedup_capacity, emit
        self.suspicious_pool: dict[str, User] = resolved
        self._seeds = tuple(resolved.values())
        self._seen: OrderedDict[tuple[str, str], str] = OrderedDict()
        self._profiles: OrderedDict[str, User] = OrderedDict()
        self._announced: set[str] = set()
        self._fetched: set[str] = set()
        self.posts: dict[str, str] = {}
        self._stream_records: dict[str, tuple[str, str]] = {}
        # Start replay before HTTP backfill so concurrent activity isn't missed.
        self.cursor = int(time() * 1_000_000)

    def _records(self, activity: ActivityBatch):
        for record in [*activity.users, *activity.contents, *activity.interactions]:
            if isinstance(record, Content) and record.content_type == 'post':
                self.posts[record.content_id] = record.user_id
            if isinstance(record, User):
                self._profiles[record.user_id] = record
                self._profiles.move_to_end(record.user_id)
                if len(self._profiles) > self.dedup_capacity:
                    self._profiles.popitem(last=False)
                if record.user_id in self.suspicious_pool:
                    self.suspicious_pool[record.user_id] = record
                kind, ident = 'User', record.user_id
            elif isinstance(record, Content):
                kind, ident = 'Content', record.content_id
            elif isinstance(record, Interaction):
                kind, ident = 'Interaction', record.interaction_id
            else:
                raise TypeError('Adapter returned an unsupported canonical record')
            key, serialized = (kind, ident), record.model_dump_json()
            changed = self._seen.get(key) != serialized
            self._seen[key] = serialized
            self._seen.move_to_end(key)
            if len(self._seen) > self.dedup_capacity:
                self._seen.popitem(last=False)
            if changed:
                self.emit({'type': kind, 'record': record.model_dump()})

    def _admit(self, user: User, source: str | None = None):
        if user.user_id not in self.suspicious_pool:
            if len(self.suspicious_pool) >= self.max_users:
                return
            self.suspicious_pool[user.user_id] = user
        if user.user_id not in self._announced:
            event = {'type': 'pool_member', 'user': user.model_dump(),
                     'reason': 'observed_interaction' if source else 'seed'}
            if source:
                event['discovered_from'] = source
            self.emit(event)
            self._announced.add(user.user_id)

    def bootstrap(self) -> None:
        """Fetch only original seeds; discovered users are monitored through the stream."""
        for index, user in enumerate(self._seeds, start=1):
            if user.user_id in self._fetched:
                continue
            logger.info('Initial seed collection %d/%d: %s', index, len(self._seeds), user.user_id)
            self._admit(user)
            self._fetched.add(user.user_id)
            try:
                result = self.platform.poll_user(user)
            except Exception as exc:
                logger.warning('Initial fetch failed for %s (%s); live monitoring continues',
                               user.user_id, type(exc).__name__)
                continue
            self._records(result.activity)
            for discovered in result.discovered_users:
                self._admit(discovered, user.user_id)

    def process_event(self, event: dict) -> None:
        """Handle a commit, fetching a profile only for a newly observed actor."""
        payload = event.get('payload', {})
        if not isinstance(payload, dict):
            raise ValueError('Invalid Jetstream payload')
        if not payload.get('$type', '').endswith('#commit'):
            return
        did, collection, rkey = payload['did'], payload['collection'], payload['rkey']
        uri = f'at://{did}/{collection}/{rkey}'
        operation = payload['operation']
        if operation == 'delete':
            tracked = self._stream_records.pop(uri, None)
            if collection == 'app.bsky.feed.post' and (uri in self.posts or ('Content', uri) in self._seen):
                self.posts.pop(uri, None)
                tracked = ('Content', uri)
            if tracked:
                kind, ident = tracked
                self._seen.pop((kind, ident), None)
                self.emit({'type': 'delete', 'record_type': kind, 'record_id': ident})
            return
        if operation not in {'create', 'update'}:
            return
        record = payload['record']
        content, interaction, source = None, None, None
        if collection == 'app.bsky.feed.post':
            parent = record.get('reply', {}).get('parent', {}).get('uri')
            if parent:
                if parent not in self.posts:
                    return  # Only direct comments on watched top-level posts.
                source = self.posts[parent]
            elif did not in self.suspicious_pool:
                return
            content = Content(content_id=uri, user_id=did,
                              content_type='comment' if parent else 'post',
                              text=record['text'], created_at=record['createdAt'], parent_id=parent)
        elif collection in {'app.bsky.feed.like', 'app.bsky.feed.repost'}:
            target = record['subject']['uri']
            if target not in self.posts:
                return
            source = self.posts[target]
            kind = 'like' if collection == 'app.bsky.feed.like' else 'share'
            interaction = Interaction(interaction_id=f'bluesky:{kind}:{target}:{did}',
                                      user_id=did, target_id=target, interaction_type=kind,
                                      created_at=record['createdAt'])
        else:
            return
        # Validate before admitting a participant or emitting activity.
        actor = self.suspicious_pool.get(did) or self._profiles.get(did)
        if actor is None:
            try:
                # BlueskyPlatform.resolve_user delegates to client.get_profile().
                actor = self.platform.resolve_user(did)
            except Exception as exc:
                logger.warning('Profile lookup failed for %s (%s); skipping activity',
                               did, type(exc).__name__)
                return
        if source:
            self._admit(actor, source)
        self._records(ActivityBatch(users=[actor], contents=[content] if content else [],
                                    interactions=[interaction] if interaction else []))
        self._stream_records[uri] = ('Content', uri) if content else ('Interaction', interaction.interaction_id)

    def subscription_url(self, endpoint: str) -> str:
        params = [('collections', collection) for collection in (
            'app.bsky.feed.post', 'app.bsky.feed.like', 'app.bsky.feed.repost')]
        params += [('kinds', 'commit'), ('cursor', str(self.cursor))]
        return endpoint + '?' + urlencode(params)

    def run(self, *,
            endpoint: str = 'wss://jetstream.us-east.bsky.network/xrpc/network.bsky.jetstream.subscribeEvents',
            connector=connect) -> None:
        self.bootstrap()
        logger.info('Initial seed collection finished; connecting to Jetstream')
        backoff = 1
        while True:
            try:
                with connector(self.subscription_url(endpoint), subprotocols=['xrpc.v1.json'],
                               open_timeout=30, close_timeout=5) as socket:
                    logger.info('Jetstream connected; listening for posts and direct interactions')
                    while True:
                        frame = socket.recv()
                        try:
                            event = json.loads(frame)
                            if not isinstance(event, dict) or not isinstance(event.get('payload'), dict):
                                raise ValueError('Invalid Jetstream envelope')
                        except (ValueError, TypeError):
                            logger.warning('Skipping malformed Jetstream envelope')
                            continue
                        try:
                            self.process_event(event)
                        except (KeyError, TypeError, ValueError, AttributeError):
                            logger.warning('Skipping malformed Jetstream record')
                        # Advance only after processing; reconnect replay is inclusive.
                        seq = event.get('payload', {}).get('seq')
                        if isinstance(seq, int):
                            self.cursor = seq
                        backoff = 1
            except (OSError, WebSocketException, TimeoutError) as exc:
                logger.warning('Jetstream disconnected (%s); reconnecting', type(exc).__name__)
                sleep(backoff)
                backoff = min(backoff * 2, 30)
