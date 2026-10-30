from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import httpx
import pytest
from atproto import Client
from atproto_client.request import Request

from app.config import Settings
from app.platforms.bluesky import BlueskyPlatform


def test_authenticated_client_uses_login_service_and_logs_in_once():
    with patch('app.platforms.bluesky.Client') as client_class:
        platform = BlueskyPlatform(handle='alice.bsky.social', app_password='app-secret')
        try:
            assert client_class.call_args.kwargs['base_url'] == 'https://bsky.social'
            client_class.return_value.login.assert_called_once_with('alice.bsky.social', 'app-secret')
        finally:
            platform.close()


@pytest.mark.parametrize('kwargs', [{'handle': 'alice.bsky.social'}, {'app_password': 'secret'},
                                   {'client': Mock(), 'handle': 'alice.bsky.social', 'app_password': 'secret'}])
def test_incomplete_or_ambiguous_credentials_are_rejected(kwargs):
    with pytest.raises(ValueError):
        BlueskyPlatform(**kwargs)


def test_login_failure_closes_owned_transport():
    with patch('app.platforms.bluesky.Client') as client_class, patch('app.platforms.bluesky.Request') as request:
        client_class.return_value.login.side_effect = RuntimeError('login failed')
        with pytest.raises(RuntimeError, match='login failed'):
            BlueskyPlatform(handle='alice.bsky.social', app_password='secret')
        request.return_value.close.assert_called_once()


def test_search_403_has_actionable_error_without_html_or_retries():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(403, content=b'<html>Forbidden</html>')

    request = Request(transport=httpx.MockTransport(handler))
    try:
        platform = BlueskyPlatform(client=Client(base_url='https://public.api.bsky.app', request=request))
        with pytest.raises(RuntimeError, match='BLUESKY_APP_PASSWORD') as error:
            platform.collect_context(['gaza'], datetime(2026, 10, 1, tzinfo=timezone.utc),
                                     datetime(2026, 10, 2, tzinfo=timezone.utc))
        assert '<html>' not in str(error.value)
        assert len(calls) == 1
    finally:
        request.close()


def test_search_omits_empty_cursor_and_preserves_page_cursor():
    client = Mock()
    client.app.bsky.feed.search_posts.side_effect = [
        NS(posts=[NS(uri='ignored', record=None)], cursor='next'), NS(posts=[], cursor=None)]
    platform = BlueskyPlatform(client=client)
    platform.collect_context(['gaza'], datetime(2026, 10, 1, tzinfo=timezone.utc),
                             datetime(2026, 10, 2, tzinfo=timezone.utc))
    first, second = client.app.bsky.feed.search_posts.call_args_list
    assert 'cursor' not in first.args[0]
    assert second.args[0]['cursor'] == 'next'


def test_settings_redact_app_password():
    settings = Settings(_env_file=None, bluesky_app_password='app-secret')
    assert 'app-secret' not in repr(settings)
    assert settings.bluesky_app_password.get_secret_value() == 'app-secret'
