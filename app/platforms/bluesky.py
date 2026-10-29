"""Read-only public Bluesky polling through the atproto SDK."""
from atproto import Client, models
from atproto_client.request import Request

from app.platforms.base import PollResult
from app.schemas import ActivityBatch, Content, User


class BlueskyPlatform:
    def __init__(self, *, page_size: int = 25, max_pages: int = 2,
                 thread_depth: int = 2, client: Client | None = None):
        if not 1 <= page_size <= 100 or max_pages < 1 or not 0 <= thread_depth <= 100:
            raise ValueError('Invalid Bluesky listing/thread limits')
        self.page_size, self.max_pages = page_size, max_pages
        self.thread_depth = thread_depth
        self._request = Request(timeout=30) if client is None else None
        self.client = client if client is not None else Client(
            base_url='https://public.api.bsky.app', request=self._request)

    def close(self):
        if self._request is not None:
            self._request.close()

    @staticmethod
    def _user(profile):
        return User(user_id=f'bluesky:user:{profile.did}',
                    display_name=profile.handle, is_synthetic=False)

    def resolve_user(self, handle: str) -> User:
        actor = handle.strip().removeprefix('bluesky:user:').removeprefix('@')
        if not actor or any(char.isspace() for char in actor) or '/' in actor:
            raise ValueError('Provide a Bluesky handle or DID')
        return self._user(self.client.get_profile(actor))

    def poll_user(self, user: User) -> PollResult:
        user = self.resolve_user(user.user_id)
        did = user.user_id.removeprefix('bluesky:user:')
        users = {user.user_id: user}
        contents, discovered, authored = {}, {}, {}

        def add(post, participant=False):
            record = post.record
            if not isinstance(record, models.AppBskyFeedPost.Record):
                return
            actor = self._user(post.author)
            users[actor.user_id] = actor
            parent = record.reply.parent.uri if record.reply else None
            content = Content(content_id=f'bluesky:{post.uri}', user_id=actor.user_id,
                              content_type='comment' if parent else 'post', text=record.text,
                              created_at=record.created_at,
                              parent_id=f'bluesky:{parent}' if parent else None)
            contents[content.content_id] = content
            if participant and actor.user_id != user.user_id:
                discovered[actor.user_id] = actor

        cursor = None
        for _ in range(self.max_pages):
            page = self.client.get_author_feed(actor=did, cursor=cursor,
                                               limit=self.page_size, filter='posts_with_replies')
            for item in page.feed:
                # An author's feed can contain reposts of someone else's text.
                if item.reason is not None or item.post.author.did != did:
                    continue
                add(item.post)
                authored[item.post.uri] = item.post
            if not page.cursor or page.cursor == cursor:
                break
            cursor = page.cursor

        # Refresh threads on every poll to observe replies to existing posts.
        for uri in authored:
            root = self.client.get_post_thread(uri=uri, depth=self.thread_depth,
                                               parent_height=2).thread
            # Ancestors are classifier context, not discovered participants.
            parent = getattr(root, 'parent', None)
            while isinstance(parent, models.AppBskyFeedDefs.ThreadViewPost):
                add(parent.post)
                parent = parent.parent
            stack = [(root, False)]
            while stack:
                node, participant = stack.pop()
                if not isinstance(node, models.AppBskyFeedDefs.ThreadViewPost):
                    continue  # Deleted and blocked thread placeholders have no content.
                add(node.post, participant=participant)
                stack.extend((reply, True) for reply in reversed(node.replies or []))

        # Hydrate missing immediate parents plus one additional ancestor level.
        attempted = set()
        for _ in range(2):
            missing = {c.parent_id for c in contents.values()
                       if c.parent_id and c.parent_id not in contents} - attempted
            if not missing:
                break
            attempted.update(missing)
            uris = sorted(parent.removeprefix('bluesky:') for parent in missing)
            for start in range(0, len(uris), 25):
                for post in self.client.get_posts(uris[start:start + 25]).posts:
                    add(post)
        return PollResult(activity=ActivityBatch(users=list(users.values()),
                          contents=list(contents.values()), interactions=[]),
                          discovered_users=list(discovered.values()))
