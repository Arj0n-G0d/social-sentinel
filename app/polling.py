"""Platform-independent polling and discovery. Output only; no graph or LLM calls."""
from collections import OrderedDict # a dictionary that lets us move entries and remove the oldest one easily.
import json
import logging
from threading import Event # Event acts as a stop signal for the worker.
from typing import Callable # Callable is data-type for functions passed as parameters.

from app.platforms.base import SocialPlatform
from app.schemas import ActivityBatch, Content, Interaction, User

logger = logging.getLogger(__name__) # __name__ = "app.polling" i.e. file name


def print_event(event: dict) -> None:
    print(json.dumps(event, ensure_ascii=False), flush=True)


class PollingWorker:
    # platform - adapter used for fetch activity
    # seeds - initial list of usernames
    # * - means remaining args must be passed by name (eg: max_users = 50)
    # max_users - max pool size
    # max_depth - discover users upto what depth ?
    """
        Depth describes how a user was discovered:
                Seed user                    depth 0 (Alice)
                └── Someone commenting       depth 1 (Bob; he comments on Alice's post)
                        └── Their commenter  depth 2 (Charlie; he comments on Bob's post)
        Max Depth = 1 -> WE MONITOR ALICE AND BOB ONLY
    """
    # dedup_capacity - remember up to *this many* records for duplicate detection.
    """
        Dedup means avoiding duplicates.

        If we fetch the same post every minute:
                - First time → print it.
                - Next time, unchanged → skip printing.
                - If its text changes → print the updated version.
        dedup_capacity=10000 means the worker remembers up to 10,000 records to make these comparisons.
    """
    # emit - function to be called. Callable[[dict], None] means “a function that accepts a dictionary and returns nothing.”

    def __init__(self, platform: SocialPlatform, seeds: list[str], *,
                 poll_interval: float = 60, max_users: int = 100,
                 max_depth: int = 1, dedup_capacity: int = 10000,
                 emit: Callable[[dict], None] = print_event):
        if not seeds or poll_interval <= 0 or max_users < 1 or max_depth < 0 or dedup_capacity < 1:
            raise ValueError('Seeds and positive polling/capacity limits are required; depth must be >= 0')
        resolved = {u.user_id: u for u in map(platform.resolve_user, seeds)}
        """
            map(platform.resolve_user, seeds) = [platform.resolve_user(seed) for seed in seeds]    
            resolved = mapping of user_id to User object
        """
        if len(resolved) > max_users:
            raise ValueError('max_users must accommodate all distinct seeds')
        self.platform, self.poll_interval = platform, poll_interval
        self.max_users, self.max_depth = max_users, max_depth
        self.dedup_capacity, self.emit = dedup_capacity, emit
        self.suspicious_pool: dict[str, User] = resolved
        self.depths = dict.fromkeys(resolved, 0) # Assigns every seed a discovery depth of 0.
        self._seen: OrderedDict[tuple[str, str], str] = OrderedDict() 
        """
                Creates an empty cache of previously seen records.
                        - Each key is (record_type, record_id).
                        - Each value is the record’s JSON string.
                Record = User or Content or Interaction
                _seen remembers the records we fetched, so repeated polling doesn’t keep printing identical data.
                First fetch: post says “Hello!”  → print and remember it
                Next fetch: still “Hello!”      → skip
                Later fetch: “Hello everyone!”  → print and remember the update
        """
        self._announced: set[str] = set()
        """
                _announced remembers whose addition to the monitoring pool we already announced.
                Alice enters the pool → print “Alice added as a seed”
                Next cycle            → don’t repeat that announcement
        """

    # Helper that processes a batch of activity
    # Basically runs emit() on new (unseen) activity
    def _records(self, activity: ActivityBatch):
        # * - unpacks array elements
        for record in [*activity.users, *activity.contents, *activity.interactions]:
            if isinstance(record, User):
                kind, ident = 'User', record.user_id
            elif isinstance(record, Content):
                kind, ident = 'Content', record.content_id
            elif isinstance(record, Interaction):
                kind, ident = 'Interaction', record.interaction_id
            else:
                raise TypeError('Adapter returned an unsupported canonical record')
            key, serialized = (kind, ident), record.model_dump_json()
            changed = self._seen.get(key) != serialized # Checking if we have seen this record in previous polls
            self._seen[key] = serialized
            self._seen.move_to_end(key)
            if len(self._seen) > self.dedup_capacity:
                self._seen.popitem(last=False) # Remembering atmost dedup_capacity records at a time
            if changed:
                self.emit({'type': kind, 'record': record.model_dump()})

    def poll_once(self) -> None:
        # A snapshot keeps expansion fair: new participants start next cycle.
        for user_id, user in list(self.suspicious_pool.items()):
            depth = self.depths[user_id]
            if user_id not in self._announced:
                self.emit({'type': 'pool_member', 'user': user.model_dump(), 'depth': depth,
                           'reason': 'seed' if depth == 0 else 'observed_interaction'})
                self._announced.add(user_id)
            try:
                result = self.platform.poll_user(user)
            except Exception as exc:
                # One unavailable account must not stop the rest. Avoid logging
                # response bodies or exception text that might contain credentials.
                logger.warning('Poll failed for %s (%s); retry next cycle', user_id, type(exc).__name__)
                continue
            self._records(result.activity)
            if depth >= self.max_depth:
                continue
            for discovered in result.discovered_users:
                if discovered.user_id in self.suspicious_pool:
                    continue
                if len(self.suspicious_pool) >= self.max_users:
                    break
                self.suspicious_pool[discovered.user_id] = discovered
                self.depths[discovered.user_id] = depth + 1
                self.emit({'type': 'pool_member', 'user': discovered.model_dump(),
                           'depth': depth + 1, 'reason': 'observed_interaction',
                           'discovered_from': user_id})
                self._announced.add(discovered.user_id)

    def run(self, stop: Event | None = None) -> None:
        stop = stop or Event()
        while not stop.is_set():
            self.poll_once()
            stop.wait(self.poll_interval)
