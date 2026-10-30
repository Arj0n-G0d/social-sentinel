import json
from contextlib import contextmanager
from urllib.parse import parse_qs, urlsplit

import pytest

from app.platforms.base import PollResult
from app.schemas import ActivityBatch, Content, User
from app.streaming import StreamingWorker

SEED = 'did:plc:seed'
READER = 'did:plc:reader'
POST = f'at://{SEED}/app.bsky.feed.post/first'


def user(did):
    return User(user_id=did, display_name=did, is_synthetic=False)


class Platform:
    def __init__(self):
        self.calls = []
        self.fail = False

    def resolve_user(self, handle):
        return user(handle)

    def poll_user(self, actor):
        self.calls.append(actor.user_id)
        if self.fail:
            raise RuntimeError('credential must not be logged')
        return PollResult(activity=ActivityBatch(users=[actor], contents=[Content(
            content_id=f'at://{actor.user_id}/app.bsky.feed.post/first', user_id=actor.user_id,
            content_type='post', text='First', created_at='2026-10-29T00:00:00Z')], interactions=[]))


def commit(did=SEED, collection='app.bsky.feed.post', key='first', record=None,
           operation='create', seq=1):
    return {'$type': 'message', 'payload': {
        '$type': 'network.bsky.jetstream.subscribeEvents#commit',
        'did': did, 'collection': collection, 'rkey': key, 'operation': operation,
        'record': record if record is not None else {'text': 'First', 'createdAt': '2026-10-29T00:00:00Z'},
        'seq': seq}}


def comment(parent=POST, did=READER, key='comment'):
    return commit(did=did, key=key, record={'text': 'Comment', 'createdAt': '2026-10-29T00:00:00Z',
                  'reply': {'parent': {'uri': parent}}})


def worker(**kwargs):
    events, platform = [], Platform()
    result = StreamingWorker(platform, [SEED], emit=events.append, **kwargs)
    result.bootstrap()
    return result, events, platform


def test_bootstrap_fetches_each_user_once_and_seed_dedup():
    events, platform = [], Platform()
    result = StreamingWorker(platform, [SEED, SEED], emit=events.append)
    result.bootstrap()
    result.bootstrap()
    assert platform.calls == [SEED]
    assert result.posts == {POST: SEED}
    assert len([e for e in events if e['type'] == 'pool_member']) == 1


def test_direct_comment_admits_user_and_nested_reply_is_ignored():
    result, events, platform = worker()
    result.process_event(comment())
    assert READER in result.suspicious_pool
    result.process_event(comment(parent=f'at://{READER}/app.bsky.feed.post/comment', did='did:plc:nested'))
    assert 'did:plc:nested' not in result.suspicious_pool
    result.bootstrap()
    result.bootstrap()
    assert platform.calls == [SEED]
    result.process_event(commit(did=READER))
    assert f'at://{READER}/app.bsky.feed.post/first' in result.posts
    result.process_event(comment(parent=f'at://{READER}/app.bsky.feed.post/first', did='did:plc:third'))
    assert 'did:plc:third' in result.suspicious_pool


@pytest.mark.parametrize('collection,kind', [('app.bsky.feed.like', 'like'), ('app.bsky.feed.repost', 'share')])
def test_unknown_actors_discovered_from_target_not_author(collection, kind):
    result, events, _ = worker()
    result.process_event(commit(did=READER, collection=collection, record={
        'subject': {'uri': POST}, 'createdAt': '2026-10-29T00:00:01Z'}))
    record = next(e['record'] for e in events if e['type'] == 'Interaction')
    assert record['target_id'] == POST
    assert record['user_id'] == READER
    assert record['interaction_type'] == kind
    assert READER in result.suspicious_pool


def test_unrelated_posts_and_interactions_on_comments_are_ignored():
    result, events, _ = worker()
    events.clear()
    result.process_event(commit(did=READER))
    result.process_event(commit(did=READER, collection='app.bsky.feed.like', record={
        'subject': {'uri': f'at://{SEED}/app.bsky.feed.post/comment'}, 'createdAt': 'now'}))
    assert events == []
    assert list(result.suspicious_pool) == [SEED]


def test_live_posts_edits_duplicates_and_deletes():
    result, events, _ = worker()
    events.clear()
    result.process_event(commit())  # Same record as HTTP backfill.
    assert events == []
    edited = commit(operation='update', record={'text': 'Edited', 'createdAt': '2026-10-29T00:00:00Z'})
    result.process_event(edited)
    result.process_event(edited)
    assert len(events) == 1
    assert events[0]['record']['text'] == 'Edited'
    deletion = commit(operation='delete')
    result.process_event(deletion)
    result.process_event(deletion)
    assert events[-1] == {'type': 'delete', 'record_type': 'Content', 'record_id': POST}
    assert POST not in result.posts


def test_delete_interaction_seen_on_stream():
    result, events, _ = worker()
    event = commit(did=READER, collection='app.bsky.feed.like', key='reaction', record={
        'subject': {'uri': POST}, 'createdAt': 'now'})
    result.process_event(event)
    ident = next(e['record']['interaction_id'] for e in events if e['type'] == 'Interaction')
    event['payload']['operation'] = 'delete'
    result.process_event(event)
    assert events[-1] == {'type': 'delete', 'record_type': 'Interaction', 'record_id': ident}


def test_cap_limits_pool_but_keeps_direct_activity():
    result, events, _ = worker(max_users=1)
    result.process_event(comment())
    result.bootstrap()
    assert list(result.suspicious_pool) == [SEED]
    assert any(e['type'] == 'Content' and e['record']['user_id'] == READER for e in events)


def test_fetch_failure_does_not_stop_live_processing(caplog):
    platform = Platform()
    platform.fail = True
    result = StreamingWorker(platform, [SEED], emit=lambda _: None)
    result.bootstrap()
    result.process_event(commit(key='live'))
    assert f'at://{SEED}/app.bsky.feed.post/live' in result.posts
    assert 'credential must not be logged' not in caplog.text


def test_reconnect_resumes_after_processed_event_and_replay_is_deduplicated(monkeypatch):
    result, events, platform = worker()
    events.clear()
    monkeypatch.setattr('app.streaming.sleep', lambda _: None)
    sockets, urls = [], []
    live = commit(key='live', seq=123)
    followup = commit(key='next', seq=124)

    class Socket:
        def __init__(self, frames):
            self.frames = iter(frames)

        def recv(self):
            try:
                item = next(self.frames)
            except StopIteration:
                raise KeyboardInterrupt()
            if isinstance(item, Exception):
                raise item
            return json.dumps(item)

    sockets = [Socket([live, OSError('disconnect')]), Socket([live, followup])]

    @contextmanager
    def connector(url, **kwargs):
        urls.append(url)
        assert kwargs['subprotocols'] == ['xrpc.v1.json']
        yield sockets.pop(0)

    with pytest.raises(KeyboardInterrupt):
        result.run(connector=connector)
    params = parse_qs(urlsplit(urls[1]).query)
    assert params['cursor'] == ['123']
    assert 'dids' not in params
    assert set(params['collections']) == {'app.bsky.feed.post', 'app.bsky.feed.like', 'app.bsky.feed.repost'}
    assert result.cursor == 124
    assert len([e for e in events if e['type'] == 'Content']) == 2
    assert platform.calls == [SEED]


@pytest.mark.parametrize('seeds,kwargs', [([], {}), ([SEED], {'max_users': 0}),
    ([SEED], {'dedup_capacity': 0}), ([SEED, READER], {'max_users': 1})])
def test_invalid_limits(seeds, kwargs):
    with pytest.raises(ValueError):
        StreamingWorker(Platform(), seeds, **kwargs)


def test_malformed_frames_do_not_stop_live_processing(caplog):
    result, events, platform = worker()
    frames = iter(['not-json', '[]', '{"payload":null}', json.dumps(commit(record={}, seq=100)),
                   json.dumps(commit(key='valid', seq=101))])

    class Socket:
        def recv(self):
            try:
                return next(frames)
            except StopIteration:
                raise KeyboardInterrupt()

    @contextmanager
    def connector(*args, **kwargs):
        yield Socket()

    with pytest.raises(KeyboardInterrupt):
        result.run(connector=connector)
    assert result.cursor == 101
    assert f'at://{SEED}/app.bsky.feed.post/valid' in result.posts
    assert 'Skipping malformed Jetstream' in caplog.text
    assert platform.calls == [SEED]


def test_backfill_only_fetches_original_seeds_despite_discoveries():
    class ChainPlatform(Platform):
        def poll_user(self, actor):
            result = super().poll_user(actor)
            result.discovered_users = [user(actor.user_id + 'x')]
            return result

    platform = ChainPlatform()
    result = StreamingWorker(platform, [SEED], max_users=3, emit=lambda _: None)
    result.bootstrap()
    result.bootstrap()
    assert platform.calls == [SEED]
    assert set(result.suspicious_pool) == {SEED, SEED + 'x'}


def test_live_discovery_never_fetches_http_activity():
    result, events, platform = worker()
    platform.poll_user = lambda _: pytest.fail('HTTP activity fetch after socket opened')
    frames = iter([comment(), commit(did=READER),
                   comment(parent=f'at://{READER}/app.bsky.feed.post/first', did='did:plc:third')])

    class Socket:
        def recv(self):
            try:
                return json.dumps(next(frames))
            except StopIteration:
                raise KeyboardInterrupt()

    @contextmanager
    def connector(*args, **kwargs):
        yield Socket()

    with pytest.raises(KeyboardInterrupt):
        result.run(connector=connector)
    assert set(result.suspicious_pool) == {SEED, READER, 'did:plc:third'}
