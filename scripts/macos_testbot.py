"""macOS native-crash testbot / debug harness.

Reproduce and debug the macOS-only segfaults (SSL/Security trust store, CoreAudio,
AppKit) *without* the GUI in the way. Run on a real Mac inside the project venv.
The mocked, cross-platform regression guard lives in tests/test_ssl_init_concurrency.py;
this script is the real thing — it builds real clients and drives real threads, so
it actually crashes when a fix is missing.

faulthandler is always on. A segfault prints every thread's C/Python stack (the
same shape as the bug report); a hang past --timeout seconds dumps all stacks and
exits, so the harness never wedges.

Modes
-----
ssl-race   Construct the REAL Cartesia + OpenAI + Anthropic clients concurrently
           in a tight loop. Building a client creates an SSL context, which needs
           no network, so this runs offline with dummy keys. Default wraps each
           build in ssl_init_lock() (proves the fix holds under stress). Pass
           --unsafe to drop the lock and reproduce the original segfault — use it
           once to confirm the harness actually catches the crash, then drop it.

query      End-to-end, headless: stream a real LLM reply on one thread while a
           second thread pumps it through TTS — the exact two-producer concurrency
           that crashed. Needs real API keys (env / config). --no-tts drains only
           the LLM side.

qt         The faithful repro: boot a minimal QApplication, register the real
           main-thread invoker (core.system.main_thread), and play a TTS stream
           from a worker thread. This recreates the Cocoa run loop + worker-thread
           conditions where opening the CoreAudio stream and building the SSL
           context collide. Needs TTS keys and an audio device.

Examples
--------
    python scripts/macos_testbot.py ssl-race --iterations 50
    python scripts/macos_testbot.py ssl-race --iterations 20 --unsafe   # expect crash pre-fix
    python scripts/macos_testbot.py query "hi how are you today"
    python scripts/macos_testbot.py query "ping" --no-tts
    python scripts/macos_testbot.py qt "hello there"
"""
from __future__ import annotations

import argparse
import contextlib
import faulthandler
import os
import queue
import sys
import threading
import time

# Run from the repo root so `core`, `ui`, `config` import.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

faulthandler.enable()

_DUMMY_KEYS = {"openai": "sk-test-0000", "cartesia": "test-0000", "anthropic": "sk-ant-test-0000"}


def _note_platform() -> None:
    if sys.platform != "darwin":
        print(f"[testbot] NOTE: sys.platform={sys.platform!r} — the segfaults are macOS-only; "
              "this run only checks the code path doesn't error.")


# ---------------------------------------------------------------------------
# ssl-race — concurrent real client construction (the SSL-context segfault)
# ---------------------------------------------------------------------------

def _build_openai(unsafe: bool) -> None:
    from core.system.native_locks import ssl_init_lock
    from core.system import sdk_clients
    cm = contextlib.nullcontext() if unsafe else ssl_init_lock()
    with cm:
        sdk_clients.openai_client(api_key=_DUMMY_KEYS["openai"], max_retries=0)


def _build_anthropic(unsafe: bool) -> None:
    from core.system.native_locks import ssl_init_lock
    from core.system import sdk_clients
    cm = contextlib.nullcontext() if unsafe else ssl_init_lock()
    with cm:
        sdk_clients.anthropic_client(api_key=_DUMMY_KEYS["anthropic"])


def _build_cartesia(unsafe: bool) -> None:
    # Construct the client only (builds httpx + SSL context) — no websocket_connect,
    # so no network/valid key is needed. This is the frame that segfaulted
    # (core/tts.py _get_cartesia_ws -> Cartesia(...) -> create_ssl_context).
    from cartesia import Cartesia  # type: ignore
    from core.system.native_locks import ssl_init_lock
    cm = contextlib.nullcontext() if unsafe else ssl_init_lock()
    with cm:
        Cartesia(api_key=_DUMMY_KEYS["cartesia"])


def _available_builders(unsafe: bool):
    """Return the build callables whose SDKs are importable in this venv."""
    builders = []
    for name, fn in (("openai", _build_openai), ("cartesia", _build_cartesia),
                     ("anthropic", _build_anthropic)):
        try:
            __import__(name)
        except Exception as exc:
            print(f"[testbot] skipping {name}: not importable ({exc})")
            continue
        builders.append(lambda u=unsafe, f=fn: f(u))
    return builders


def run_ssl_race(iterations: int, unsafe: bool) -> int:
    _note_platform()
    builders = _available_builders(unsafe)
    if len(builders) < 2:
        print("[testbot] need at least two SDKs installed to race; aborting.")
        return 2
    mode = "UNSAFE (no lock — expecting the crash pre-fix)" if unsafe else "locked (fixed path)"
    print(f"[testbot] ssl-race: {iterations} iterations, {len(builders)} concurrent builders, {mode}")

    for i in range(1, iterations + 1):
        barrier = threading.Barrier(len(builders))
        errors: list[BaseException] = []

        def worker(build):
            try:
                barrier.wait()          # release all builders at the same instant
                build()
            except BaseException as exc:  # noqa: BLE001 - report, keep looping
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(b,)) for b in builders]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        if errors:
            print(f"[testbot] iteration {i}: {len(errors)} error(s): "
                  f"{errors[0]!r}")
        if i % 10 == 0 or i == iterations:
            print(f"[testbot] survived {i}/{iterations} iterations")

    print("[testbot] ssl-race completed without a segfault.")
    return 0


# ---------------------------------------------------------------------------
# query — real LLM stream + TTS, the two-producer concurrency, headless
# ---------------------------------------------------------------------------

def run_query(prompt: str, with_tts: bool, timeout: float) -> int:
    _note_platform()
    from core.llm_clients import client as llm
    from core import tts as tts_module

    # Prewarm sequentially first (this is what the app does at startup).
    print("[testbot] prewarming clients (sequential)...")
    if with_tts:
        try:
            tts_module.prewarm()
        except Exception as exc:
            print(f"[testbot] tts.prewarm failed (continuing): {exc!r}")
    llm.prewarm()

    faulthandler.dump_traceback_later(timeout, exit=True)
    text_q: "queue.Queue[str | None]" = queue.Queue()

    def llm_producer():
        try:
            for chunk in llm.stream_response(prompt, None, use_tools=False):
                sys.stdout.write(chunk)
                sys.stdout.flush()
                text_q.put(chunk)
        except Exception as exc:
            print(f"\n[testbot] llm_producer error: {exc!r}")
        finally:
            text_q.put(None)

    def _chunks():
        while True:
            item = text_q.get()
            if item is None:
                return
            yield item

    print(f"[testbot] query: {prompt!r} (tts={'on' if with_tts else 'off'})\n--- reply ---")
    producer = threading.Thread(target=llm_producer, daemon=True)
    producer.start()

    audio_bytes = 0
    if with_tts:
        # Consume the live LLM stream through TTS concurrently — same shape as the app.
        try:
            for pcm in tts_module.stream_audio_from_chunks(_chunks()):
                audio_bytes += len(pcm)
        except Exception as exc:
            print(f"\n[testbot] tts error: {exc!r}")
    else:
        for _ in _chunks():
            pass
    producer.join(timeout=timeout)

    faulthandler.cancel_dump_traceback_later()
    print(f"\n--- done --- (tts produced {audio_bytes} audio bytes)")
    return 0


# ---------------------------------------------------------------------------
# qt — real QApplication + main-thread invoker + worker-thread playback
# ---------------------------------------------------------------------------

def run_qt(prompt: str, timeout: float) -> int:
    _note_platform()
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QTimer
    from core import audio
    from core import tts as tts_module
    from core.llm_clients import client as llm
    import main as app_main  # module-level only; main() is not called

    qt = QApplication(sys.argv)

    # Register the REAL main-thread invoker — this is the piece that hops the
    # CoreAudio stream open/close onto the GUI thread (and is implicated in the
    # original crash). On macOS main.py does exactly this at startup.
    invoker = app_main._MainThreadInvoker()
    audio.set_main_thread_runner(invoker.run_on_main)

    print("[testbot] prewarming clients (sequential)...")
    try:
        tts_module.prewarm()
    except Exception as exc:
        print(f"[testbot] tts.prewarm failed (continuing): {exc!r}")
    llm.prewarm()

    faulthandler.dump_traceback_later(timeout, exit=True)
    done = threading.Event()

    def worker():
        text_q: "queue.Queue[str | None]" = queue.Queue()

        def llm_producer():
            try:
                for chunk in llm.stream_response(prompt, None, use_tools=False):
                    sys.stdout.write(chunk)
                    sys.stdout.flush()
                    text_q.put(chunk)
            except Exception as exc:
                print(f"\n[testbot] llm_producer error: {exc!r}")
            finally:
                text_q.put(None)

        def chunks():
            while True:
                item = text_q.get()
                if item is None:
                    return
                yield item

        threading.Thread(target=llm_producer, daemon=True).start()
        # Drives the audio producer thread + opens CoreAudio via run_on_main.
        audio.play_tts_stream_from_chunks(chunks(), on_done=done.set)

    print(f"[testbot] qt: {prompt!r}\n--- reply ---")
    threading.Thread(target=worker, daemon=True).start()

    # Quit the Qt loop once playback finishes or the timeout elapses.
    def poll():
        if done.wait(0):
            faulthandler.cancel_dump_traceback_later()
            print("\n--- done ---")
            qt.quit()
    timer = QTimer()
    timer.timeout.connect(poll)
    timer.start(100)
    QTimer.singleShot(int(timeout * 1000), qt.quit)

    qt.exec()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="macOS native-crash testbot / debug harness")
    parser.add_argument("--timeout", type=float, default=60.0,
                        help="seconds before faulthandler dumps all stacks and exits (default 60)")
    sub = parser.add_subparsers(dest="mode", required=True)

    p_race = sub.add_parser("ssl-race", help="concurrent real client construction (SSL segfault)")
    p_race.add_argument("--iterations", type=int, default=50)
    p_race.add_argument("--unsafe", action="store_true",
                        help="drop the lock to reproduce the crash (confirms the harness catches it)")

    p_query = sub.add_parser("query", help="real LLM stream + TTS, headless")
    p_query.add_argument("prompt")
    p_query.add_argument("--no-tts", dest="tts", action="store_false", help="drain LLM only")

    p_qt = sub.add_parser("qt", help="QApplication + main-thread invoker + worker playback")
    p_qt.add_argument("prompt")

    args = parser.parse_args(argv)

    if args.mode == "ssl-race":
        return run_ssl_race(args.iterations, args.unsafe)
    if args.mode == "query":
        return run_query(args.prompt, args.tts, args.timeout)
    if args.mode == "qt":
        return run_qt(args.prompt, args.timeout)
    parser.error(f"unknown mode {args.mode!r}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
