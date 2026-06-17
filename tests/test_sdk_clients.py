"""Tests for test sdk clients."""

import sys
import types
import unittest
import urllib.request
from unittest import mock

from core.system import sdk_clients


class SDKClientTests(unittest.TestCase):
    """Test case for s d k client tests behavior."""
    def setUp(self):
        """Verify set up behavior."""
        self._orig_getproxies = urllib.request.getproxies
        self._orig_installed = sdk_clients._proxy_guard_installed
        sdk_clients._proxy_guard_installed = False
        self.addCleanup(self._restore_proxy_guard)

    def _restore_proxy_guard(self):
        """Verify restore proxy guard behavior."""
        urllib.request.getproxies = self._orig_getproxies
        sdk_clients._proxy_guard_installed = self._orig_installed

    def test_openai_client_disables_env_proxy_lookup_in_macos_safe_mode(self):
        """Verify openai client disables env proxy lookup in macos safe mode behavior."""
        httpx_calls = []
        openai_calls = []

        class FakeHttpxClient:
            """Client for fake httpx client communication."""
            def __init__(self, **kwargs):
                """Initialize the fake httpx client instance."""
                httpx_calls.append(kwargs)

        class FakeOpenAI:
            """Test case for fake open a i behavior."""
            def __init__(self, **kwargs):
                """Initialize the fake open a i instance."""
                openai_calls.append(kwargs)

        fake_httpx = types.SimpleNamespace(Client=FakeHttpxClient)
        fake_openai = types.SimpleNamespace(OpenAI=FakeOpenAI)

        with mock.patch.object(sdk_clients.macos_safety.sys, "platform", "darwin"), \
             mock.patch.dict(sdk_clients.os.environ, {}, clear=True), \
             mock.patch.dict(sys.modules, {"httpx": fake_httpx, "openai": fake_openai}):
            sdk_clients.openai_client(api_key="test")

        self.assertEqual(httpx_calls, [{"trust_env": False}])
        self.assertIn("http_client", openai_calls[0])
        self.assertIs(urllib.request.getproxies, urllib.request.getproxies_environment)

    def test_existing_http_client_is_preserved(self):
        """Verify existing http client is preserved behavior."""
        openai_calls = []

        class FakeOpenAI:
            """Test case for fake open a i behavior."""
            def __init__(self, **kwargs):
                """Initialize the fake open a i instance."""
                openai_calls.append(kwargs)

        fake_openai = types.SimpleNamespace(OpenAI=FakeOpenAI)
        existing = object()

        with mock.patch.object(sdk_clients.macos_safety.sys, "platform", "darwin"), \
             mock.patch.dict(sdk_clients.os.environ, {}, clear=True), \
             mock.patch.dict(sys.modules, {"openai": fake_openai}):
            sdk_clients.openai_client(api_key="test", http_client=existing)

        self.assertIs(openai_calls[0]["http_client"], existing)


if __name__ == "__main__":
    unittest.main()
