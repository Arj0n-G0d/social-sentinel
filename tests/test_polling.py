import httpx
import pytest

from app.platforms.base import PollResult
from app.platforms.reddit import RedditPlatform
from app.polling import PollingWorker
from app.schemas import ActivityBatch, Content, Interaction, User
from app.workflow import contexts


def listing(children, after=None):
    return {'data': {'children': children, 'after': after}}


def post(ident='p', author='Seed', text='Post body'):
    return {'kind': 't3', 'data': {'id': ident, 'name': f't3_{ident}',
            'author': author, 'title': 'Title', 'selftext': text, 'created_utc': 1700000000}}


def comment(ident='c', author='Reader', parent='t3_p', replies=''):
    return {'kind': 't1', 'data': {'id': ident, 'name': f't1_{ident}',
            'author': author, 'body': 'Reply', 'parent_id': parent,
            'created_utc': 1700000001, 'replies': replies}}


@pytest.fixture
def reddit_factory(monkeypatch):
    monkeypatch.setattr('app.platforms.reddit.time.sleep', lambda _: None)
    clients = []

    def factory(handler, **kwargs):
        client = httpx.Client(transport=httpx.MockTransport(handler))
        clients.append(client)
        return RedditPlatform('id', 'secret', 'test-agent', client=client, **kwargs)

    yield factory
    for client in clients:
        client.close()


def test_reddit_normalization_discovery_and_classifier_context(reddit_factory):
    requests = []

    def handler(request):
        requests.append(request)
        path = request.url.path
        if path == '/api/v1/access_token':
            return httpx.Response(200, json={'access_token': 'token', 'expires_in': 3600})
        assert request.headers['Authorization'] == 'Bearer token'
        assert request.headers['User-Agent'] == 'test-agent'
        if path.endswith('/submitted'):
            return httpx.Response(200, json=listing([post()]))
        if path.endswith('/comments'):
            return httpx.Response(200, json=listing([comment('own', 'Seed', 't1_parent')]))
        if path == '/comments/p':
            nested = comment('nested', 'Other', 't1_c')
            return httpx.Response(200, json=[listing([post()]), listing([
                comment(replies=listing([nested])), comment('deleted', '[deleted]'),
                {'kind': 'more', 'data': {'children': ['unfetched']}}])])
        if path == '/api/info':
            assert request.url.params['id'] == 't1_parent'
            return httpx.Response(200, json=listing([comment('parent', 'ContextAuthor')]))
        raise AssertionError(path)

    platform = reddit_factory(handler)
    result = platform.poll_user(platform.resolve_user('/u/Seed'))
    assert {u.user_id for u in result.discovered_users} == {'reddit:user:reader', 'reddit:user:other'}
    assert all(not u.is_synthetic for u in result.activity.users)
    assert result.activity.interactions == []  # No synthetic votes or comment interactions.
    assert all(isinstance(c, Content) for c in result.activity.contents)
    rows = list(contexts(result.activity.contents, result.activity.interactions))
    own = next(ctx for _, ident, ctx in rows if ident == 'reddit:t1_own')
    assert own['parent_text'] == 'Reply'
    reply = next(ctx for _, ident, ctx in rows if ident == 'reddit:t1_c')
    assert reply == {'kind': 'comment', 'text': 'Reply', 'parent_text': 'Title\n\nPost body'}
    assert sum(r.url.path == '/api/v1/access_token' for r in requests) == 1


def test_canonical_batch_is_directly_compatible_with_classifier():
    platform = FakePlatform()
    activity = platform.poll_user(platform.resolve_user('seed')).activity
    rows = list(contexts(activity.contents, activity.interactions))
    share = next(ctx for label, _, ctx in rows if label == 'Interaction')
    assert share['kind'] == 'share' and share['target_text'] == 'First'


def test_reddit_listing_pagination_is_bounded(reddit_factory):
    pages = []

    def handler(request):
        if request.url.path.endswith('access_token'):
            return httpx.Response(200, json={'access_token': 'token', 'expires_in': 3600})
        pages.append(request.url.params.get('after'))
        return httpx.Response(200, json=listing([], after=f't3_{len(pages)}'))

    platform = reddit_factory(handler, max_pages=2)
    assert list(platform._listing('/user/seed/submitted')) == []
    assert pages == [None, 't3_1']


def test_reddit_rate_limit_and_expired_auth(reddit_factory):
    statuses = iter([401, 429])
    tokens = []

    def handler(request):
        if request.url.path.endswith('access_token'):
            tokens.append(True)
            return httpx.Response(200, json={'access_token': 'token', 'expires_in': 3600})
        return httpx.Response(next(statuses), headers={'retry-after': '120'})

    platform = reddit_factory(handler)
    for _ in range(2):
        with pytest.raises(httpx.HTTPStatusError):
            platform._get('/user/seed/submitted')
    assert len(tokens) == 2
    import time
    assert platform._next_request > time.monotonic() + 100


class FakePlatform:
    def __init__(self):
        self.calls = []
        self.text = 'First'
        self.fail = False

    def resolve_user(self, handle):
        return User(user_id=handle.lower(), display_name=handle.lower(), is_synthetic=False)

    def poll_user(self, user):
        self.calls.append(user.user_id)
        if self.fail and user.user_id == 'seed':
            raise RuntimeError('do not log secrets')
        reader = self.resolve_user('reader')
        return PollResult(activity=ActivityBatch(users=[user, reader], contents=[Content(
            content_id='post', user_id=user.user_id, content_type='post',
            text=self.text, created_at='2026-10-24T00:00:00+00:00')], interactions=[Interaction(
                interaction_id='share', user_id=reader.user_id, target_id='post',
                interaction_type='share', created_at='2026-10-24T00:00:01+00:00')]),
            discovered_users=[reader])


def test_worker_discovery_dedup_edits_and_canonical_interactions():
    platform, events = FakePlatform(), []
    worker = PollingWorker(platform, ['Seed', 'SEED'], emit=events.append)
    worker.poll_once()
    assert platform.calls == ['seed']
    assert set(worker.suspicious_pool) == {'seed', 'reader'}
    assert any(e['type'] == 'Interaction' and e['record']['interaction_type'] == 'share' for e in events)
    events.clear()
    # Keep fake content's author stable to test unchanged records across polls.
    worker.max_depth = 0
    worker.suspicious_pool.pop('reader')
    worker.poll_once()
    assert events == []
    platform.text = 'Edited'
    worker.poll_once()
    assert len(events) == 1 and events[0]['record']['text'] == 'Edited'


def test_worker_polls_discovered_users_and_continues_after_failure(caplog):
    platform = FakePlatform()
    worker = PollingWorker(platform, ['seed'], emit=lambda _: None)
    worker.poll_once()
    platform.fail = True
    worker.poll_once()
    assert platform.calls == ['seed', 'seed', 'reader']
    assert 'do not log secrets' not in caplog.text


@pytest.mark.parametrize('kwargs', [{'max_depth': 0}, {'max_users': 1}])
def test_worker_discovery_limits(kwargs):
    worker = PollingWorker(FakePlatform(), ['seed'], emit=lambda _: None, **kwargs)
    worker.poll_once()
    assert list(worker.suspicious_pool) == ['seed']


def test_worker_requires_valid_limits():
    with pytest.raises(ValueError):
        PollingWorker(FakePlatform(), [], emit=lambda _: None)
    with pytest.raises(ValueError):
        PollingWorker(FakePlatform(), ['a', 'b'], max_users=1)
