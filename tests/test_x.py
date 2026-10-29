import httpx
import pytest

from app.platforms.x import XPlatform


def post(ident, author, parent=None):
    return {'id': ident, 'author_id': author, 'text': f'Text {ident}',
            'created_at': '2026-10-25T00:00:00Z',
            'referenced_tweets': [{'type': 'replied_to', 'id': parent}] if parent else []}


def test_x_poll_normalization_pagination_context_and_discovery(monkeypatch):
    monkeypatch.setattr('app.platforms.x.time.sleep', lambda _: None)
    pages = []

    def handler(request):
        assert request.headers['Authorization'] == 'Bearer test-token'
        path = request.url.path
        if path == '/2/users/by/username/Seed':
            return httpx.Response(200, json={'data': {'id': '1', 'username': 'Seed'}})
        if path == '/2/users/1/tweets':
            assert request.url.params['exclude'] == 'retweets'
            pages.append(request.url.params.get('pagination_token'))
            return httpx.Response(200, json={'data': [post('10', '1', '30')],
                                            'meta': {'next_token': 'next'}})
        if path == '/2/users/1/mentions':
            repost = post('21', '2')
            repost['referenced_tweets'] = [{'type': 'retweeted', 'id': '10'}]
            return httpx.Response(200, json={'data': [post('20', '2', '10'), repost],
                    'includes': {'users': [{'id': '2', 'username': 'Reader'}]}})
        if path == '/2/tweets':
            parent = request.url.params['ids']
            return httpx.Response(200, json={'data': [post(parent, '3', '40' if parent == '30' else None)],
                    'includes': {'users': [{'id': '3', 'username': 'Context'}]}})
        raise AssertionError(path)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        platform = XPlatform('test-token', client=client)
        user = platform.resolve_user('@Seed')
        result = platform.poll_user(user)
    assert pages == [None, 'next']
    assert user.user_id == 'x:user:1'
    assert {c.content_id for c in result.activity.contents} == {'x:post:10', 'x:post:20', 'x:post:30', 'x:post:40'}
    assert result.activity.contents[0].parent_id == 'x:post:30'
    assert result.activity.contents[0].content_type == 'comment'
    assert [u.user_id for u in result.discovered_users] == ['x:user:2']
    assert result.activity.interactions == []
    assert all(not u.is_synthetic for u in result.activity.users)


@pytest.mark.parametrize('kwargs', [{'bearer_token': ''}, {'bearer_token': 't', 'page_size': 4},
                                   {'bearer_token': 't', 'max_pages': 0}])
def test_x_invalid_configuration(kwargs):
    with pytest.raises(ValueError):
        XPlatform(**kwargs)


def test_x_errors_and_backoff(monkeypatch):
    monkeypatch.setattr('app.platforms.x.time.sleep', lambda _: None)
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(
            429, headers={'retry-after': '120'}))) as client:
        platform = XPlatform('t', client=client)
        with pytest.raises(httpx.HTTPStatusError):
            platform.resolve_user('Seed')
        import time
        assert platform._next_request > time.monotonic() + 100
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(
            200, json={'errors': [{'detail': 'Unavailable'}]}))) as client:
        platform = XPlatform('t', client=client)
        with pytest.raises(ValueError, match='without data'):
            platform.resolve_user('Seed')
        with pytest.raises(ValueError, match='username'):
            platform.resolve_user('invalid/name')
