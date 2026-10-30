from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from atproto import models
from atproto.exceptions import AtProtocolError

from app.platforms.bluesky import BlueskyPlatform
from app.workflow import contexts
from app.schemas import User


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


def test_bluesky_direct_activity_only():
    client = Mock()
    client.get_profile.return_value = NS(did=DID, handle='seed.bsky.social')
    own = post('own')
    reply = post('reply', 'reader', own.uri)
    nested = post('nested', 'nested', reply.uri)
    client.get_author_feed.side_effect = [
        NS(feed=[NS(post=own, reason=None), NS(post=post('repost', 'other'), reason=NS()),
                 NS(post=post('authored-reply', parent=uri('external')), reason=None)], cursor='next'),
        NS(feed=[NS(post=own, reason=None)], cursor='next')]
    client.get_post_thread.return_value = NS(thread=thread(own, replies=[
        thread(reply, replies=[thread(nested)]), thread(nested),
        models.AppBskyFeedDefs.NotFoundPost(uri=uri('gone'), not_found=True)]))
    liker = NS(did='did:plc:liker', handle='liker.bsky.social')
    sharer = NS(did='did:plc:sharer', handle='sharer.bsky.social')
    client.get_likes.side_effect = [
        NS(likes=[NS(actor=liker, created_at='2026-10-25T00:00:01Z')], cursor='next'),
        NS(likes=[], cursor='next')]
    client.get_reposted_by.return_value = NS(reposted_by=[sharer], cursor=None)
    platform = BlueskyPlatform(client=client)
    result = platform.poll_user(platform.resolve_user('@seed.bsky.social'))
    client.get_profile.assert_called_once_with('seed.bsky.social')
    assert client.get_author_feed.call_args.kwargs['actor'] == DID
    assert client.get_author_feed.call_args.kwargs['filter'] == 'posts_no_replies'
    client.get_post_thread.assert_called_once_with(uri=own.uri, depth=1, parent_height=0)
    client.get_posts.assert_not_called()
    assert {c.content_id for c in result.activity.contents} == {own.uri, reply.uri}
    assert {u.user_id for u in result.discovered_users} == {
        'did:plc:reader', 'did:plc:liker', 'did:plc:sharer'}
    assert {i.interaction_type for i in result.activity.interactions} == {'like', 'share'}
    assert all(i.target_id == own.uri for i in result.activity.interactions)
    by_id = {c.content_id: c for c in result.activity.contents}
    assert by_id[own.uri].user_id == DID
    assert by_id[own.uri].parent_id is None
    assert by_id[reply.uri].user_id == 'did:plc:reader'
    assert by_id[reply.uri].parent_id == own.uri
    assert all(i.user_id.startswith('did:') for i in result.activity.interactions)
    assert client.get_post_thread.call_args.kwargs['uri'] == by_id[own.uri].content_id
    assert client.get_likes.call_count == 2
    rows = list(contexts(result.activity.contents, result.activity.interactions))
    assert next(ctx for _, ident, ctx in rows if ident == reply.uri)['parent_text'] == 'Text own'
    assert all(not u.is_synthetic for u in result.activity.users)


def test_bluesky_empty_feed():
    client = Mock()
    client.get_profile.return_value = NS(did=DID, handle='seed.bsky.social')
    client.get_author_feed.return_value = NS(feed=[], cursor=None)
    result = BlueskyPlatform(client=client).poll_user(User(user_id=DID, display_name='seed.bsky.social', is_synthetic=False))
    client.get_profile.assert_not_called()
    assert client.get_author_feed.call_args.kwargs['actor'] == DID
    assert result.activity.contents == []
    assert result.discovered_users == []
    client.get_post_thread.assert_not_called()
    client.get_likes.assert_not_called()
    client.get_reposted_by.assert_not_called()


@pytest.mark.parametrize('kwargs', [{'page_size': 0}, {'max_pages': 0}])
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
        assert platform.resolve_user('@seed.bsky.social').user_id == DID
    finally:
        request.close()

    platform = BlueskyPlatform()
    platform._request = Mock(wraps=platform._request)
    platform.close()
    platform._request.close.assert_called_once()


def test_repeated_polling_uses_resolved_and_discovered_native_ids():
    client = Mock()
    client.get_profile.return_value = NS(did=DID, handle='seed.bsky.social')
    own = post('own')
    reply = post('reply', 'reader', own.uri)
    client.get_author_feed.return_value = NS(feed=[NS(post=own, reason=None)], cursor=None)
    client.get_post_thread.return_value = NS(thread=thread(own, replies=[thread(reply)]))
    client.get_likes.return_value = NS(likes=[], cursor=None)
    client.get_reposted_by.return_value = NS(reposted_by=[], cursor=None)
    platform = BlueskyPlatform(client=client)
    user = platform.resolve_user('@seed.bsky.social')
    assert user.user_id == DID
    discovered = platform.poll_user(user).discovered_users[0]
    platform.poll_user(user)
    client.get_author_feed.return_value = NS(feed=[], cursor=None)
    platform.poll_user(discovered)
    client.get_profile.assert_called_once_with('seed.bsky.social')
    assert [call.kwargs['actor'] for call in client.get_author_feed.call_args_list] == [
        DID, DID, 'did:plc:reader']


@pytest.mark.parametrize('handle', ['handles', 'username', '', '@', 'invalid/handle'])
def test_invalid_account_input_is_rejected_before_api_call(handle):
    client = Mock()
    platform = BlueskyPlatform(client=client)
    with pytest.raises(ValueError, match='full handle'):
        platform.resolve_user(handle)
    client.get_profile.assert_not_called()


@pytest.mark.parametrize('display_name,expected', [('Alice Example', 'Alice Example'), ('', 'seed.bsky.social'), (None, 'seed.bsky.social')])
def test_profile_display_name_with_handle_fallback(display_name, expected):
    client = Mock()
    client.get_profile.return_value = NS(did=DID, handle='seed.bsky.social', display_name=display_name)
    assert BlueskyPlatform(client=client).resolve_user(DID).display_name == expected
    client.get_profile.assert_called_once_with(DID)
