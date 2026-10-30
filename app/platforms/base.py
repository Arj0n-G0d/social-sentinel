from typing import Protocol

from pydantic import BaseModel, Field

from app.schemas import ActivityBatch, User


class PollResult(BaseModel):
    activity: ActivityBatch
    # Only authors of direct comments, likes, or shares on the polled user’s posts.
    discovered_users: list[User] = Field(default_factory=list)


class SocialPlatform(Protocol):
    """Adapters own authentication, native IDs, pagination and normalization.

    Fetch only authored top-level posts and their direct comments/interactions.
    Do not explore replies to comments or fetch ancestor threads.
    User.user_id is the native API-ready ID; resolve handles once in resolve_user.
    poll_user uses that ID directly without another profile lookup. Content IDs,
    author IDs, parent IDs, and interaction targets are native API-ready IDs.
    Interaction IDs are stable deduplication keys. Comments are Content with a
    parent_id; Interaction is reserved for likes, dislikes and shares. Live users
    must have is_synthetic=False. Initial fetches may overlap stream records.
    """

    def resolve_user(self, handle: str) -> User: ...

    def poll_user(self, user: User) -> PollResult: ...

    def close(self) -> None: ...
