"""Regression tests for the macOS SSL-context-construction race.

Background: on macOS, building an SDK client creates an SSL context, which reads
the system trust store through the Security framework. Doing that from two
threads at once segfaults. A single query already runs the LLM stream and the TTS
stream on separate threads, so two cold clients could build their SSL contexts
concurrently on the first request and crash (see core.system.native_locks).

The fix is a single process-wide lock (ssl_init_lock) shared across all SDK
client construction, plus per-provider caching so the build happens once. These
tests pin that contract. They fake sys.platform/darwin via native_locks._IS_MAC
and mock the SDK constructors, so they run on any host OS — no network, no keys,
no openai/cartesia/anthropic install required.

The *real* concurrent-construction repro (which actually segfaults without the
fix) lives in scripts/macos_testbot.py and must be run on a Mac.
"""
import contextlib
import sys
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock

import config
import core.llm_clients.client as llm
import core.system.native_locks as native_locks
import core.tts as tts_module
from core import optional_deps
from core.system.native_locks import keychain_lock, native_init_lock, ssl_init_lock


class _ConcurrencyProbe:
    """Records the maximum number of threads simultaneously inside build()."""

    def __init__(self, hold: float = 0.02):
        """Initialize the concurrency probe instance."""
        self._lock = threading.Lock()
        self.active = 0
        self.max_active = 0
        self.hold = hold

    def build(self):
        """Verify build behavior."""
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        time.sleep(self.hold)  # widen the window for any overlap to show up
        with self._lock:
            self.active -= 1


def _run_concurrently(fn, n: int = 8) -> list[Exception]:
    """Start n threads that all call fn() at the same instant; return any errors."""
    barrier = threading.Barrier(n)
    errors: list[Exception] = []

    def worker():
        """Verify worker behavior."""
        try:
            barrier.wait()
            fn()
        except Exception as exc:  # pragma: no cover - surfaced via assertion
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return errors


class SslInitLockTests(unittest.TestCase):
    """Test case for ssl init lock tests behavior."""
    def test_lock_is_noop_off_macos(self):
        """Verify lock is noop off macos behavior."""
        with mock.patch.object(native_locks, "_IS_MAC", False):
            self.assertIsInstance(ssl_init_lock(), contextlib.nullcontext)

    def test_returns_one_shared_lock_on_macos(self):
        """Verify returns one shared lock on macos behavior."""
        with mock.patch.object(native_locks, "_IS_MAC", True):
            self.assertIs(ssl_init_lock(), native_locks._ssl_init_lock)
            self.assertIs(ssl_init_lock(), ssl_init_lock())
            self.assertIs(native_init_lock(), native_locks._ssl_init_lock)
            self.assertIs(keychain_lock(), native_locks._ssl_init_lock)

    def test_tts_and_llm_use_the_same_lock_function(self):
        # Both modules import the singleton helper by reference, so a build in
        # TTS and a build in the LLM client serialize against each other.
        """Verify tts and llm use the same lock function behavior."""
        self.assertIs(tts_module.ssl_init_lock, llm.ssl_init_lock)

    def test_lock_serializes_concurrent_builds_on_macos(self):
        """Verify lock serializes concurrent builds on macos behavior."""
        probe = _ConcurrencyProbe()
        with mock.patch.object(native_locks, "_IS_MAC", True):
            def build():
                """Verify build behavior."""
                with ssl_init_lock():
                    probe.build()
            errors = _run_concurrently(build, n=8)
        self.assertEqual(errors, [])
        self.assertEqual(probe.max_active, 1)  # never two SSL builds at once

    def test_lock_allows_overlap_off_macos(self):
        # Sanity: off-macOS the no-op context must NOT serialize (otherwise the
        # macOS-only test above would be meaningless). nullcontext lets threads
        # overlap, so max_active should climb above 1.
        """Verify lock allows overlap off macos behavior."""
        probe = _ConcurrencyProbe(hold=0.05)
        with mock.patch.object(native_locks, "_IS_MAC", False):
            def build():
                """Verify build behavior."""
                with ssl_init_lock():
                    probe.build()
            errors = _run_concurrently(build, n=8)
        self.assertEqual(errors, [])
        self.assertGreater(probe.max_active, 1)


class DynamicClientCachingTests(unittest.TestCase):
    """Test case for dynamic client caching tests behavior."""
    def setUp(self):
        """Verify set up behavior."""
        llm.reset_clients()
        self.addCleanup(llm.reset_clients)

    def test_openai_client_built_once_under_lock_then_cached(self):
        """Verify openai client built once under lock then cached behavior."""
        calls: list[str] = []

        def fake_build(provider):
            """Verify fake build behavior."""
            calls.append(provider)
            # On macOS the SSL lock must be held while the client is constructed.
            self.assertTrue(native_locks._ssl_init_lock.locked())
            return ("client", provider)

        with mock.patch.object(native_locks, "_IS_MAC", True), \
             mock.patch.object(llm, "_build_dynamic_openai_client", fake_build):
            a = llm._dynamic_openai_client("openai")
            b = llm._dynamic_openai_client("openai")

        self.assertIs(a, b)
        self.assertEqual(calls, ["openai"])  # built once, served from cache after

    def test_openai_client_cached_per_provider(self):
        """Verify openai client cached per provider behavior."""
        with mock.patch.object(llm, "_build_dynamic_openai_client",
                               side_effect=lambda p: ("client", p)) as build:
            groq = llm._dynamic_openai_client("groq")
            openai = llm._dynamic_openai_client("openai")
            groq_again = llm._dynamic_openai_client("groq")
        self.assertEqual(groq, ("client", "groq"))
        self.assertEqual(openai, ("client", "openai"))
        self.assertIs(groq, groq_again)
        self.assertEqual(build.call_count, 2)  # one per distinct provider

    def test_concurrent_first_use_builds_openai_client_once(self):
        # The real failure mode: many threads racing the very first build.
        """Verify concurrent first use builds openai client once behavior."""
        build_count = []

        def fake_build(provider):
            """Verify fake build behavior."""
            build_count.append(provider)
            time.sleep(0.02)
            return object()

        with mock.patch.object(native_locks, "_IS_MAC", True), \
             mock.patch.object(llm, "_build_dynamic_openai_client", fake_build):
            errors = _run_concurrently(lambda: llm._dynamic_openai_client("openai"), n=8)

        self.assertEqual(errors, [])
        self.assertEqual(len(build_count), 1)  # cache + lock => single build

    def test_anthropic_client_built_once_and_cached(self):
        """Verify anthropic client built once and cached behavior."""
        created: list[dict] = []

        class FakeAnthropic:
            """Test case for fake anthropic behavior."""
            def __init__(self, **kwargs):
                """Initialize the fake anthropic instance."""
                created.append(kwargs)

        fake_mod = types.ModuleType("anthropic")
        fake_mod.Anthropic = FakeAnthropic

        with mock.patch.object(native_locks, "_IS_MAC", True), \
             mock.patch.dict(sys.modules, {"anthropic": fake_mod}):
            a = llm._dynamic_anthropic_client()
            b = llm._dynamic_anthropic_client()

        self.assertIs(a, b)
        self.assertEqual(len(created), 1)

    def test_reset_clients_clears_dynamic_caches(self):
        """Verify reset clients clears dynamic caches behavior."""
        llm._dynamic_openai_clients["openai"] = object()
        llm._dynamic_anthropic_client_cache = object()
        llm.reset_clients()
        self.assertEqual(llm._dynamic_openai_clients, {})
        self.assertIsNone(llm._dynamic_anthropic_client_cache)

    def test_stdlib_openai_compat_request_runs_under_native_lock(self):
        """Verify stdlib openai compat request runs under native lock behavior."""
        probe = self

        class _FakeResponse:
            """Test case for fake response behavior."""
            def __init__(self):
                """Initialize the fake response instance."""
                self.headers = {"Content-Encoding": ""}

            def read(self):
                """Verify read behavior."""
                probe.assertTrue(native_locks._ssl_init_lock.locked())
                return b'{"choices":[{"message":{"content":"ok"}}]}'

            def __enter__(self):
                """Enter the context manager."""
                return self

            def __exit__(self, exc_type, exc, tb):
                """Exit the context manager."""
                return False

        class _FakeOpener:
            """Test case for fake opener behavior."""
            def open(self, _request, timeout=0):
                """Verify open behavior."""
                return _FakeResponse()

        with mock.patch.object(native_locks, "_IS_MAC", True), \
             mock.patch.object(llm, "_openai_compat_base_url", return_value="https://example.test"), \
             mock.patch.object(llm, "_openai_compat_api_key", return_value="k"), \
             mock.patch("urllib.request.build_opener", return_value=_FakeOpener()):
            self.assertEqual(
                llm._openai_compat_stdlib_completion_text("google", {"model": "m", "messages": []}),
                "ok",
            )

    def test_stdlib_openai_compat_request_uses_cached_certifi_ssl_context(self):
        """Verify stdlib openai compat request uses cached certifi ssl context behavior."""
        probe = self
        llm._openai_compat_stdlib_ssl_context = None
        self.addCleanup(setattr, llm, "_openai_compat_stdlib_ssl_context", None)
        context = object()
        build_calls = []
        handler_contexts = []

        class _FakeCertifi(types.ModuleType):
            """Test case for fake certifi behavior."""
            def where(self):
                """Verify where behavior."""
                return "/tmp/cacert.pem"

        class _FakeResponse:
            """Test case for fake response behavior."""
            headers = {"Content-Encoding": ""}

            def read(self):
                """Verify read behavior."""
                return b'{"choices":[{"message":{"content":"ok"}}]}'

            def __enter__(self):
                """Enter the context manager."""
                return self

            def __exit__(self, exc_type, exc, tb):
                """Exit the context manager."""
                return False

        class _FakeOpener:
            """Test case for fake opener behavior."""
            def open(self, _request, timeout=0):
                """Verify open behavior."""
                return _FakeResponse()

        class _FakeHTTPSHandler:
            """Test case for fake h t t p s handler behavior."""
            def __init__(self, *, context):
                """Initialize the fake h t t p s handler instance."""
                handler_contexts.append(context)

        def fake_create_default_context(*, cafile=None):
            """Verify fake create default context behavior."""
            probe.assertEqual(cafile, "/tmp/cacert.pem")
            build_calls.append(cafile)
            return context

        with mock.patch.dict(sys.modules, {"certifi": _FakeCertifi("certifi")}), \
             mock.patch.object(llm._ssl, "create_default_context", fake_create_default_context), \
             mock.patch.object(llm._urllib_request, "HTTPSHandler", _FakeHTTPSHandler), \
             mock.patch.object(llm, "_openai_compat_base_url", return_value="https://example.test"), \
             mock.patch.object(llm, "_openai_compat_api_key", return_value="k"), \
             mock.patch("urllib.request.build_opener", return_value=_FakeOpener()):
            for _ in range(2):
                self.assertEqual(
                    llm._openai_compat_stdlib_completion_text("google", {"model": "m", "messages": []}),
                    "ok",
                )

        self.assertEqual(build_calls, ["/tmp/cacert.pem"])
        self.assertEqual(handler_contexts, [context, context])

    def test_stdlib_ssl_context_ignores_broken_optional_certifi(self):
        """A broken optional certifi layer should not break urllib SSL setup."""
        llm._openai_compat_stdlib_ssl_context = None
        self.addCleanup(setattr, llm, "_openai_compat_stdlib_ssl_context", None)
        context = object()
        optional_path = "C:\\app\\python_packages"
        original_path = list(sys.path)

        class _FallbackCertifi(types.ModuleType):
            """Test case for fallback certifi behavior."""
            def where(self):
                """Verify where behavior."""
                return "/fallback/cacert.pem"

        def fake_import(name, *args, **kwargs):
            """Fail certifi import only while the optional layer is on sys.path."""
            if name == "certifi" and optional_path in sys.path:
                raise PermissionError("broken optional certifi")
            if name == "certifi":
                return _FallbackCertifi("certifi")
            return original_import(name, *args, **kwargs)

        def fake_create_default_context(*, cafile=None):
            """Verify fallback certifi path is used."""
            self.assertEqual(cafile, "/fallback/cacert.pem")
            return context

        original_import = __import__
        sys.path.insert(0, optional_path)
        self.addCleanup(lambda: setattr(sys, "path", original_path))
        with mock.patch("builtins.__import__", fake_import), \
             mock.patch.object(optional_deps, "OPTIONAL_PACKAGES_DIR", Path(optional_path)), \
             mock.patch.object(llm._ssl, "create_default_context", fake_create_default_context):
            self.assertIs(llm._get_openai_compat_stdlib_ssl_context(), context)


class PrewarmTests(unittest.TestCase):
    """Test case for prewarm tests behavior."""
    def setUp(self):
        """Verify set up behavior."""
        llm.reset_clients()
        self.addCleanup(llm.reset_clients)

    def test_prewarm_builds_openai_compat_provider(self):
        """Verify prewarm builds openai compat provider behavior."""
        calls: list[str] = []
        with mock.patch.object(config, "LLM_PROVIDER", "groq"), \
             mock.patch.object(llm, "_dynamic_openai_client", lambda p: calls.append(p)):
            llm.prewarm()
        self.assertEqual(calls, ["groq"])

    def test_prewarm_builds_anthropic_provider(self):
        """Verify prewarm builds anthropic provider behavior."""
        calls: list[str] = []
        with mock.patch.object(config, "LLM_PROVIDER", "anthropic"), \
             mock.patch.object(llm, "_dynamic_anthropic_client", lambda: calls.append("a")):
            llm.prewarm()
        self.assertEqual(calls, ["a"])

    def test_prewarm_skips_codex_providers(self):
        # chatgpt/copilot use the Codex transport; prewarm must not try to build
        # an OpenAI/Anthropic SSL client for them.
        """Verify prewarm skips codex providers behavior."""
        oai, ant = [], []
        with mock.patch.object(config, "LLM_PROVIDER", "chatgpt"), \
             mock.patch.object(llm, "_dynamic_openai_client", lambda p: oai.append(p)), \
             mock.patch.object(llm, "_dynamic_anthropic_client", lambda: ant.append(1)):
            llm.prewarm()
        self.assertEqual((oai, ant), ([], []))

    def test_prewarm_is_best_effort_on_error(self):
        """Verify prewarm is best effort on error behavior."""
        def boom(_provider):
            """Verify boom behavior."""
            raise RuntimeError("no API key configured")
        with mock.patch.object(config, "LLM_PROVIDER", "openai"), \
             mock.patch.object(llm, "_dynamic_openai_client", boom):
            llm.prewarm()  # must swallow — a bad key cannot crash startup


if __name__ == "__main__":
    unittest.main()
