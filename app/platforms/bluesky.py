"""Read-only public Bluesky initial collection through the atproto SDK."""
from atproto import Client, models
from atproto_client.request import Request

from app.platforms.base import PollResult
from app.schemas import ActivityBatch, Content, Interaction, User


class BlueskyPlatform:
    def __init__(self, *, page_size: int = 25, max_pages: int = 2,
                 client: Client | None = None):
        if not 1 <= page_size <= 100 or max_pages < 1:
            raise ValueError('Invalid Bluesky listing limits')
        self.page_size, self.max_pages = page_size, max_pages
        self._request = Request(timeout=30) if client is None else None
        self.client = client if client is not None else Client(
            base_url='https://public.api.bsky.app', request=self._request)

    def close(self):
        if self._request is not None:
            self._request.close()

    @staticmethod
    def _user(profile):
        return User(user_id=profile.did,
                    display_name=profile.handle, is_synthetic=False)

    def resolve_user(self, handle: str) -> User:
        """Resolve a handle once and store the API-ready DID."""
        actor = handle.strip().removeprefix('@')
        if (not actor or any(char.isspace() for char in actor) or '/' in actor
                or not ('.' in actor or actor.startswith('did:'))):
            raise ValueError(
                f'Invalid Bluesky account {handle!r}. Provide a full handle such as '
                'alice.bsky.social or a DID, not the argument name or a short username.')
        return self._user(self.client.get_profile(actor))

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
