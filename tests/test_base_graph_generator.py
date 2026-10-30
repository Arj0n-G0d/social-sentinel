from datetime import datetime, timezone
from importlib import import_module
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from app.platforms.bluesky import BlueskyPlatform
from test_bluesky import post, thread

BaseGraphGenerator = import_module('base-graph-generation.base-graph-generator').BaseGraphGenerator
START = datetime(2026, 10, 25, tzinfo=timezone.utc)
END = datetime(2026, 10, 26, tzinfo=timezone.utc)


def client_with_posts():
    client = Mock()
    own = post('own')
    reply = post('reply', 'reader', own.uri)
    outside = post('old', 'old')
    outside.record.created_at = '2026-10-24T23:59:59Z'
    client.app.bsky.feed.search_posts.return_value = NS(posts=[own, own, outside], cursor='repeat')
    client.get_post_thread.return_value = NS(thread=thread(own, replies=[thread(reply)]))
    client.get_likes.return_value = NS(likes=[
        NS(actor=NS(did='did:plc:liker', handle='liker.bsky.social'), created_at='2026-10-25T12:00:00Z'),
        NS(actor=NS(did='did:plc:late', handle='late.bsky.social'), created_at='2026-10-26T00:00:00Z')], cursor=None)
    return client, own, reply


def test_context_timeline_deduplication_and_direct_edges():
    client, own, reply = client_with_posts()
    activity = BlueskyPlatform(client=client).collect_context(['gaza', 'gaza'], START, END)
    assert {p.content_id for p in activity.contents} == {own.uri, reply.uri}
    assert activity.contents[1].parent_id == own.uri
    assert len(activity.interactions) == 1
    assert activity.interactions[0].target_id == own.uri
    assert len(activity.users) == 3
    assert all(not u.is_synthetic for u in activity.users)
    assert client.app.bsky.feed.search_posts.call_count == 2
    params = client.app.bsky.feed.search_posts.call_args.args[0]
    assert params['q'] == 'gaza'
    assert params['since'] == START.isoformat()
    client.get_reposted_by.assert_not_called()


@pytest.mark.parametrize('users,posts,interactions,requests', [(1, 1, 0, 1), (2, 2, 1, 3), (10, 10, 10, 2)])
def test_all_global_budgets(users, posts, interactions, requests):
    client, _, _ = client_with_posts()
    result = BlueskyPlatform(client=client).collect_context(
        ['gaza', 'ceasefire'], START, END, max_users=users,
        max_posts=posts, max_interactions=interactions, max_requests=requests)
    assert len(result.users) <= users
    assert len(result.contents) <= posts
    assert len(result.interactions) <= interactions
    calls = sum(m.call_count for m in [client.app.bsky.feed.search_posts,
                                      client.get_post_thread, client.get_likes])
    assert calls <= requests
    assert {p.user_id for p in result.contents} <= {u.user_id for u in result.users}
    assert {i.target_id for i in result.interactions} <= {p.content_id for p in result.contents}


def test_generator_ingests_once_and_keeps_injected_resources_open():
    client, _, _ = client_with_posts()
    store = Mock()
    store.ingest.side_effect = lambda batch: {'users': len(batch.users), 'contents': len(batch.contents),
                                             'interactions': len(batch.interactions)}
    platform = BlueskyPlatform(client=client)
    result = BaseGraphGenerator(['gaza'], graph_store=store, platform=platform).generate(START, END)
    assert result['counts'] == {'users': 3, 'contents': 2, 'interactions': 1}
    store.ingest.assert_called_once()
    store.close.assert_not_called()
    store.initialize.assert_not_called()


def test_failure_does_not_write():
    platform, store = Mock(), Mock()
    platform.collect_context.side_effect = RuntimeError('Unavailable')
    with pytest.raises(RuntimeError):
        BaseGraphGenerator(['gaza'], graph_store=store, platform=platform).generate(START, END)
    store.ingest.assert_not_called()


@pytest.mark.parametrize('kwargs', [{'max_posts': 0}, {'max_users': -1}, {'max_requests': 0},
                                   {'max_interactions': -1}, {'page_size': 101}, {'max_pages': 0}])
def test_invalid_limits_do_not_collect(kwargs):
    platform = Mock()
    with pytest.raises(ValueError):
        BaseGraphGenerator(['gaza'], platform=platform).generate(START, END, **kwargs)
    platform.collect_context.assert_not_called()


def test_invalid_window_does_not_collect():
    platform = Mock()
    generator = BaseGraphGenerator(['gaza'], platform=platform)
    for start, end in [(END, START), (START, START), ('2026-10-25', END)]:
        with pytest.raises(ValueError):
            generator.generate(start, end)
    platform.collect_context.assert_not_called()
