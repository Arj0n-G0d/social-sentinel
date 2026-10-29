from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from atproto import models
from atproto.exceptions import AtProtocolError

from app.platforms.bluesky import BlueskyPlatform
from app.workflow import contexts


DID = 'did:plc:seed'


def uri(ident, author='seed'):
    return f'at://did:plc:{author}/app.bsky.feed.post/{ident}'


def post(ident, author='seed', parent=None):
    record = {'text': f'Text {ident}', 'createdAt': '2026-10-25T00:00:00Z'}
    if parent:
        record['reply'] = {'parent': {'uri': parent, 'cid': 'cid'},
                           'root': {'uri': parent, 'cid': 'cid'}}
    return models.AppBskyFeedDefs.PostView.model_validate({
        'uri': uri(ident, author), 'cid': 'cid',
        'author': {'did': f'did:plc:{author}', 'handle': f'{author}.bsky.social'},
        'record': models.AppBskyFeedPost.Record.model_validate(record),
        'indexedAt': '2026-10-25T00:00:00Z'})


def thread(item, **kwargs):
    return models.AppBskyFeedDefs.ThreadViewPost(post=item, **kwargs)


def test_bluesky_pagination_threads_parents_and_discovery():
    client = Mock()
    client.get_profile.return_value = NS(did=DID, handle='seed.bsky.social')
    own = post('own', parent=uri('parent', 'context'))
    reply = post('reply', 'reader', own.uri)
    client.get_author_feed.side_effect = [
        NS(feed=[NS(post=own, reason=None), NS(post=post('repost', 'other'), reason=NS())], cursor='next'),
        NS(feed=[NS(post=own, reason=None)], cursor='next')]
    blocked = models.AppBskyFeedDefs.NotFoundPost(uri=uri('gone'), not_found=True)
    client.get_post_thread.return_value = NS(thread=thread(own, replies=[thread(reply), blocked]))
    client.get_posts.side_effect = [NS(posts=[post('parent', 'context', uri('grand', 'context'))]),
                                   NS(posts=[post('grand', 'context')])]
    platform = BlueskyPlatform(client=client)
    user = platform.resolve_user('@seed.bsky.social')
    result = platform.poll_user(user)
    assert client.get_profile.call_args.args == (DID,)
    assert client.get_author_feed.call_args.kwargs['cursor'] == 'next'
    assert client.get_post_thread.call_count == 1
    assert len(result.activity.contents) == 4
    assert result.activity.interactions == []
    assert [u.user_id for u in result.discovered_users] == ['bluesky:user:did:plc:reader']
    assert all(not u.is_synthetic for u in result.activity.users)
    rows = list(contexts(result.activity.contents, []))
    own_context = next(context for _, ident, context in rows if ident == f'bluesky:{own.uri}')
    assert own_context['parent_text'] == 'Text parent'
    platform.close()


def test_bluesky_ancestors_are_context_only_and_empty_feed():
    client = Mock()
    client.get_profile.return_value = NS(did=DID, handle='seed.bsky.social')
    own = post('own', parent=uri('parent', 'context'))
    client.get_author_feed.return_value = NS(feed=[NS(post=own, reason=None)], cursor=None)
    client.get_post_thread.return_value = NS(thread=thread(own, parent=thread(post('parent', 'context'))))
    result = BlueskyPlatform(client=client).poll_user(NS(user_id=DID))
    assert len(result.activity.contents) == 2
    assert result.discovered_users == []
    client.get_posts.assert_not_called()
    client.get_author_feed.return_value = NS(feed=[], cursor=None)
    assert BlueskyPlatform(client=client).poll_user(NS(user_id=DID)).activity.contents == []


@pytest.mark.parametrize('kwargs', [{'page_size': 0}, {'max_pages': 0}, {'thread_depth': 101}])
def test_bluesky_invalid_limits(kwargs):
    with pytest.raises(ValueError):
        BlueskyPlatform(**kwargs)


def test_bluesky_errors_propagate():
    client = Mock()
    client.get_profile.side_effect = AtProtocolError('Unavailable')
    platform = BlueskyPlatform(client=client)
    with pytest.raises(AtProtocolError):
        platform.resolve_user('seed.bsky.social')
    with pytest.raises(ValueError):
        platform.resolve_user('invalid/handle')


def test_bluesky_sdk_public_transport_and_cleanup():
    import httpx
    from atproto import Client
    from atproto_client.request import Request

    def handler(request):
        assert request.url.host == 'public.api.bsky.app'
        assert request.url.path == '/xrpc/app.bsky.actor.getProfile'
        assert request.url.params['actor'] == 'seed.bsky.social'
        assert 'authorization' not in request.headers
        return httpx.Response(200, json={'did': DID, 'handle': 'seed.bsky.social'})

    request = Request(transport=httpx.MockTransport(handler))
    try:
        platform = BlueskyPlatform(client=Client(base_url='https://public.api.bsky.app', request=request))
        assert platform.resolve_user('@seed.bsky.social').user_id == f'bluesky:user:{DID}'
    finally:
        request.close()

    platform = BlueskyPlatform()
    platform._request = Mock(wraps=platform._request)
    platform.close()
    platform._request.close.assert_called_once()
