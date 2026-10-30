"""Read-only public Bluesky initial collection through the atproto SDK."""
from datetime import datetime, timezone
from atproto import Client, models
from atproto.exceptions import RequestException, UnauthorizedError
from atproto_client.request import Request

from app.platforms.base import PollResult
from app.schemas import ActivityBatch, Content, Interaction, User


class BlueskyPlatform:
    def __init__(self, *, page_size: int = 25, max_pages: int = 2,
                 client: Client | None = None, handle: str = '', app_password: str = ''):
        if not 1 <= page_size <= 100 or max_pages < 1:
            raise ValueError('Invalid Bluesky listing limits')
        if bool(handle) != bool(app_password):
            raise ValueError('Set both BLUESKY_HANDLE and BLUESKY_APP_PASSWORD')
        if client is not None and handle:
            raise ValueError('Pass an authenticated client or credentials, not both')
        self.page_size, self.max_pages = page_size, max_pages
        self._request = Request(timeout=30) if client is None else None
        self.client = client if client is not None else Client(
            base_url='https://bsky.social' if handle else 'https://public.api.bsky.app',
            request=self._request)
        if handle:
            try:
                self.client.login(handle, app_password)
            except Exception:
                self.close()
                raise

    def _search_posts(self, params):
        try:
            return self.client.app.bsky.feed.search_posts(params)
        except (RequestException, UnauthorizedError) as exc:
            if exc.response is None or exc.response.status_code != 403:
                raise
            raise RuntimeError(
                'Bluesky searchPosts returned HTTP 403. Anonymous search may be restricted. '
                'For the graph generator, set BLUESKY_HANDLE and BLUESKY_APP_PASSWORD '
                'in .env to use an authenticated session. If already authenticated, '
                'check service availability and account/network access before retrying. '
                'No graph data from this collection has been ingested.') from None

    def close(self):
        if self._request is not None:
            self._request.close()

    @staticmethod
    def _user(profile):
        return User(user_id=profile.did,
                    display_name=profile.handle,
                    is_synthetic=False)

    def resolve_user(self, handle: str) -> User:
        """Resolve a handle once and store the API-ready DID."""
        actor = handle.strip().removeprefix('@')
        if (not actor or any(char.isspace() for char in actor) or '/' in actor
                or not ('.' in actor or actor.startswith('did:'))):
            raise ValueError(
                f'Invalid Bluesky account {handle!r}. Provide a full handle such as '
                'alice.bsky.social or a DID, not the argument name or a short username.')
        return self._user(self.client.get_profile(actor))

    def collect_context(self, context: list[str], since: datetime, until: datetime, *,
                        max_users: int = 200, max_posts: int = 200,
                        max_interactions: int = 500, max_requests: int = 50) -> ActivityBatch:
        """Bounded event search plus direct replies and dated likes.

        The time window is [since, until). Reposts are omitted because the public
        reposted-by endpoint does not supply their timestamps. Limits are per run,
        including all queries and all enrichment requests, not database-wide quotas.
        """
        terms = list(dict.fromkeys(term.strip() for term in context if term.strip()))
        if not terms:
            raise ValueError('At least one non-empty context query is required')
        if (since.tzinfo is None or until.tzinfo is None or since >= until):
            raise ValueError('Provide timezone-aware timestamps with since < until')
        limits = (max_users, max_posts, max_requests)
        if any(type(value) is not int or value < 1 for value in limits) or (
                type(max_interactions) is not int or max_interactions < 0):
            raise ValueError('Collection limits must be positive integers (interactions may be zero)')
        users, contents, interactions = {}, {}, {}
        requests = 0

        def in_window(value):
            try:
                stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
                return stamp.tzinfo is not None and since <= stamp < until
            except (ValueError, TypeError, AttributeError):
                return False

        def add_user(profile):
            actor = self._user(profile)
            if actor.user_id not in users and len(users) >= max_users:
                return None
            users[actor.user_id] = actor
            return actor

        def add_post(post):
            if post.uri in contents:
                return False
            record = post.record
            if (len(contents) >= max_posts or
                    not isinstance(record, models.AppBskyFeedPost.Record) or
                    not in_window(record.created_at)):
                return False
            actor = add_user(post.author)
            if actor is None:
                return False
            parent = record.reply.parent.uri if record.reply else None
            contents[post.uri] = Content(
                content_id=post.uri, user_id=actor.user_id,
                content_type='comment' if parent else 'post', text=record.text,
                created_at=record.created_at, parent_id=parent)
            return True

        # Search first so enrichment cannot exhaust the budget before discovery.
        for term in terms:
            cursor, seen = None, set()
            for _ in range(self.max_pages):
                if requests >= max_requests or len(contents) >= max_posts:
                    break
                requests += 1
                params = {
                    'q': term, 'since': since.astimezone(timezone.utc).isoformat(),
                    'until': until.astimezone(timezone.utc).isoformat(),
                    'sort': 'latest', 'limit': min(self.page_size, max_posts - len(contents))}
                if cursor is not None:
                    params['cursor'] = cursor
                page = self._search_posts(params)
                for post in page.posts:
                    add_post(post)
                if not page.posts or not page.cursor or page.cursor in seen:
                    break
                seen.add(page.cursor)
                cursor = page.cursor

        for uri in list(contents):
            if requests < max_requests and len(contents) < max_posts:
                requests += 1
                root = self.client.get_post_thread(uri=uri, depth=1, parent_height=0).thread
                if isinstance(root, models.AppBskyFeedDefs.ThreadViewPost):
                    for reply in root.replies or []:
                        if (isinstance(reply, models.AppBskyFeedDefs.ThreadViewPost)
                                and isinstance(reply.post.record, models.AppBskyFeedPost.Record)
                                and reply.post.record.reply is not None
                                and reply.post.record.reply.parent.uri == uri):
                            add_post(reply.post)
            cursor, seen = None, set()
            for _ in range(self.max_pages):
                if requests >= max_requests or len(interactions) >= max_interactions:
                    break
                requests += 1
                page = self.client.get_likes(
                    uri=uri, cursor=cursor,
                    limit=min(self.page_size, max_interactions - len(interactions)))
                for like in page.likes:
                    if len(interactions) >= max_interactions:
                        break
                    if not in_window(like.created_at):
                        continue
                    actor = add_user(like.actor)
                    if actor is None:
                        continue
                    ident = f'bluesky:like:{uri}:{actor.user_id}'
                    interactions[ident] = Interaction(
                        interaction_id=ident, user_id=actor.user_id, target_id=uri,
                        interaction_type='like', created_at=like.created_at)
                if not page.likes or not page.cursor or page.cursor in seen:
                    break
                seen.add(page.cursor)
                cursor = page.cursor
        return ActivityBatch(users=list(users.values()), contents=list(contents.values()),
                             interactions=list(interactions.values()))

    def poll_user(self, user: User) -> PollResult:
        """Use the stored native ID directly, including for discovered users."""
        users = {user.user_id: user}
        contents, discovered, authored, interactions = {}, {}, {}, {}

        def add(post, participant=False):
            record = post.record
            if not isinstance(record, models.AppBskyFeedPost.Record):
                return
            actor = self._user(post.author)
            users[actor.user_id] = actor
            parent = record.reply.parent.uri if record.reply else None
            content = Content(content_id=post.uri, user_id=actor.user_id,
                              content_type='comment' if parent else 'post', text=record.text,
                              created_at=record.created_at,
                              parent_id=parent)
            contents[content.content_id] = content
            if participant and actor.user_id != user.user_id:
                discovered[actor.user_id] = actor

        cursor = None
        for _ in range(self.max_pages):
            page = self.client.get_author_feed(actor=user.user_id, cursor=cursor,
                                               limit=self.page_size, filter='posts_no_replies')
            for item in page.feed:
                # An author's feed can contain reposts of someone else's text.
                if (item.reason is not None or item.post.author.did != user.user_id
                        or not isinstance(item.post.record, models.AppBskyFeedPost.Record)
                        or item.post.record.reply is not None):
                    continue
                add(item.post)
                authored[item.post.uri] = item.post
            if not page.cursor or page.cursor == cursor:
                break
            cursor = page.cursor

        # Collect existing direct activity during the one-time backfill.
        for uri in authored:
            root = self.client.get_post_thread(uri=uri, depth=1, parent_height=0).thread
            if isinstance(root, models.AppBskyFeedDefs.ThreadViewPost):
                for reply in root.replies or []:
                    if (isinstance(reply, models.AppBskyFeedDefs.ThreadViewPost)
                            and isinstance(reply.post.record, models.AppBskyFeedPost.Record)
                            and reply.post.record.reply is not None
                            and reply.post.record.reply.parent.uri == uri):
                        add(reply.post, participant=True)

            for kind, fetch in [('like', self.client.get_likes),
                                ('share', self.client.get_reposted_by)]:
                cursor = None
                for _ in range(self.max_pages):
                    page = fetch(uri=uri, cursor=cursor, limit=self.page_size)
                    entries = page.likes if kind == 'like' else page.reposted_by
                    for entry in entries:
                        actor = self._user(entry.actor if kind == 'like' else entry)
                        users[actor.user_id] = actor
                        if actor.user_id != user.user_id:
                            discovered[actor.user_id] = actor
                        ident = f'bluesky:{kind}:{uri}:{actor.user_id}'
                        interactions[ident] = Interaction(
                            interaction_id=ident, user_id=actor.user_id,
                            target_id=uri, interaction_type=kind,
                            # Reposted-by profiles do not include the repost time.
                            created_at=entry.created_at if kind == 'like' else '')
                    if not page.cursor or page.cursor == cursor:
                        break
                    cursor = page.cursor
        return PollResult(activity=ActivityBatch(users=list(users.values()),
                          contents=list(contents.values()), interactions=list(interactions.values())),
                          discovered_users=list(discovered.values()))
