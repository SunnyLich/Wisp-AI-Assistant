"""Real LLM query through the real brain worker process.

Simulates what the app does when the user asks something: spawn the actual
``runtime.workers.brain_host`` subprocess, validate the configured route
(``brain.llm.test``, same as Settings' Test button), then stream a real
``brain.query`` and assert reply chunks arrive. Uses the user's configured
provider + OS-stored/.env key. Spends a few real tokens.

The worker runs with WISP_REPO_ROOT pointed at a scratch dir (with .env copied
in) so memory/chat writes never touch the real data. Memory is additionally
disabled for the query; flow_e2e covers the memory-enabled path.
"""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _lab

_lab.bootstrap()

MARKER = "pineapple"
# The assistant-language setting localizes replies (a real Wisp behavior this
# check should survive), so demand the literal untranslated token.
PROMPT = f"Reply with exactly this literal lowercase token, untranslated: {MARKER}"


def main() -> int:
    import config
    from runtime.supervisor.ipc import WorkerClient, WorkerSpec

    # WISP_TESTLAB_LLM_PROVIDER/MODEL pin a lab-only route (e.g. a free
    # AI Studio model) so the lab never spends paid tokens even when the app
    # itself is configured for a paid provider. Default: the app's own route.
    provider = (
        os.environ.get("WISP_TESTLAB_LLM_PROVIDER", "").strip()
        or str(getattr(config, "LLM_PROVIDER", "") or "").strip()
    )
    model = (
        os.environ.get("WISP_TESTLAB_LLM_MODEL", "").strip()
        or str(getattr(config, "LLM_MODEL", "") or "").strip()
    )
    fallbacks = str(getattr(config, "LLM_FALLBACKS", "") or "").strip()
    if not provider or not model:
        return _lab.finish(_lab.SKIP, "no LLM provider/model configured in .env")
    _lab.log(f"route under test: {provider} / {model} (fallbacks: {fallbacks or 'none'})")

    isolated_root = _lab.isolated_repo_root("llm_query")
    # .env never overrides existing process env, so pinning the route here
    # wins inside the worker's own config load.
    route_env = {
        "LLM_PROVIDER": provider,
        "LLM_MODEL": model,
        "CHAT_LLM_PROVIDER": provider,
        "CHAT_LLM_MODEL": model,
    }
    worker = WorkerClient(
        WorkerSpec(
            "lab-brain",
            "runtime.workers.brain_host",
            "brain",
            env=_lab.env_overrides(isolated_root=isolated_root, extra=route_env),
        )
    )
    watch = _lab.Stopwatch()
    try:
        ping = worker.call("brain.ping", {"value": "lab"}, timeout=90.0)
        _lab.log(f"brain worker up after {watch.lap()}s: ping={ping}")

        test = worker.call(
            "brain.llm.test",
            {"provider": provider, "model": model, "fallbacks": fallbacks},
            timeout=120.0,
        )
        test_ok = bool(isinstance(test, dict) and test.get("ok"))
        test_message = str((test or {}).get("message") or "") if isinstance(test, dict) else str(test)
        _lab.log(f"brain.llm.test after {watch.lap()}s: ok={test_ok} - {test_message}")
        if not test_ok:
            return _lab.finish(_lab.FAIL, f"LLM route test failed: {test_message}")

        chunks: list[str] = []
        chunk_event = threading.Event()

        def on_event(event: str, data, _req_id=None) -> None:
            if event == "reply.chunk":
                text = str((data or {}).get("text") or "") if isinstance(data, dict) else str(data or "")
                chunks.append(text)
                chunk_event.set()
            else:
                _lab.log(f"event: {event}")

        result = worker.call_with_events(
            "brain.query",
            {
                "intent_prompt": PROMPT,
                "memory_enabled": False,
                "use_tools": False,
            },
            timeout=240.0,
            on_event=on_event,
        )
        elapsed = watch.lap()
        text = str((result or {}).get("text") or "") if isinstance(result, dict) else ""
        _lab.log(f"brain.query finished after {elapsed}s: {len(chunks)} chunks, {len(text)} chars")
        _lab.log(f"reply text: {text[:200]!r}")

        if not text.strip():
            return _lab.finish(_lab.FAIL, "brain.query returned empty text")
        if not chunks:
            return _lab.finish(_lab.FAIL, "no reply.chunk events arrived (streaming path broken)")
        joined = "".join(chunks)
        if joined.strip() and joined.strip() not in text and text.strip() not in joined:
            _lab.log("note: chunk stream and final text differ (thought filtering?)")

        marker_matched = MARKER in text.lower()
        if not marker_matched:
            _lab.log(f"note: model did not echo the marker word (reply: {text[:80]!r})")
        return _lab.finish(
            _lab.PASS,
            f"{provider}/{model} streamed {len(chunks)} chunks in {elapsed}s",
            provider=provider,
            model=model,
            chunks=len(chunks),
            chars=len(text),
            marker_matched=marker_matched,
            seconds=elapsed,
        )
    finally:
        worker.shutdown()
        _lab.log("brain worker shut down")


if __name__ == "__main__":
    raise SystemExit(main())
