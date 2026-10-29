from typing import Protocol

from pydantic import BaseModel, Field

from app.schemas import ActivityBatch, User


class PollResult(BaseModel):
    activity: ActivityBatch
    # Only observed participants, not authors fetched solely for parent context.
    discovered_users: list[User] = Field(default_factory=list)


class SocialPlatform(Protocol):
    """Adapters own authentication, native IDs, pagination and normalization.

    IDs must be platform-namespaced and stable. Comments are Content with a
    parent_id; Interaction is reserved for likes, dislikes and shares. Live users
    must have is_synthetic=False. Polls may return overlapping records and edits.
    """

    def resolve_user(self, handle: str) -> User: ...

    def poll_user(self, user: User) -> PollResult: ...

    def close(self) -> None: ...
