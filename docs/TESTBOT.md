# macOS crash testbot & debugging playbook

The app has a recurring class of bug: **macOS-only native-handle crashes** that
never appear on the Windows dev box. They come from touching a macOS framework
unsafely from a worker thread:

- **CoreAudio / PortAudio** — opening/closing an output stream off the main thread.
- **AppKit / Quartz** — activating apps, posting CGEvents, enumerating windows.
- **Security framework / SSL trust store** — building an SDK client's SSL context
  (`ssl.create_default_context()`) from two threads at once.

The defenses live in [`core/system/main_thread.py`](../core/system/main_thread.py)
(hop native work onto the GUI thread) and
[`core/system/native_locks.py`](../core/system/native_locks.py) (serialize SSL
context construction). Both are **no-ops off macOS**, so the bugs are invisible
until the code runs on a Mac.

This testbot gives two layers for catching and debugging them.

## Layer 1 — regression tests (run anywhere, every CI run)

[`tests/test_ssl_init_concurrency.py`](../tests/test_ssl_init_concurrency.py)
fakes `sys.platform`/darwin and mocks the SDK constructors, so the macOS
serialization + caching contract is verified on Windows/Linux/CI with no network,
keys, or pyobjc.

```
python -m pytest tests/test_ssl_init_concurrency.py -q
```

There is also [`scripts/macos_smoke.py`](../scripts/macos_smoke.py) (run on a real
Mac / the macOS CI runner) that imports the platform-sensitive modules and calls
the real pyobjc window helpers.

These prove the *wiring*. They cannot reproduce a real segfault — mocks don't
touch the Security framework. That's Layer 2.

## Layer 2 — the on-Mac harness ([`scripts/macos_testbot.py`](../scripts/macos_testbot.py))

Run inside the project venv **on a real Mac**. `faulthandler` is always on, so a
segfault prints every thread's stack (the same shape as a crash report) and a
hang past `--timeout` dumps all stacks and exits.

### `ssl-race` — the SSL-context segfault, offline
Builds the real Cartesia + OpenAI + Anthropic clients concurrently in a loop.
Constructing a client creates its SSL context, which needs no network, so this
runs with dummy keys.

```
# Fixed path — should survive every iteration:
python scripts/macos_testbot.py ssl-race --iterations 50

# Drop the lock to reproduce the original crash (confirms the harness catches it):
python scripts/macos_testbot.py ssl-race --iterations 20 --unsafe
```

If `--unsafe` segfaults and the default run is clean, the `ssl_init_lock` fix is
doing its job.

### `query` — real LLM stream + TTS concurrency, headless
Streams a real reply on one thread while a second pumps it through TTS — the exact
two-producer path that crashed. Needs real API keys.

```
python scripts/macos_testbot.py query "hi how are you today"
python scripts/macos_testbot.py query "ping" --no-tts
```

### `qt` — the faithful repro
Boots a minimal `QApplication`, registers the **real** main-thread invoker
(`main._MainThreadInvoker` → `core.system.main_thread`), and plays a TTS stream
from a worker thread. This recreates the Cocoa-run-loop + worker-thread conditions
where opening the CoreAudio stream and building the SSL context collide. Needs TTS
keys and an audio device.

```
python scripts/macos_testbot.py qt "hello there"
```

## Debugging workflow on the Mac

1. **Reproduce headless first.** Try `ssl-race`, then `query`. If it only crashes
   with the GUI/run-loop involved, use `qt`.
2. **Read the faulthandler dump.** Find the thread whose top frames are *not*
   parked in `wait`/`run_on_main` — that's the crasher. Note the framework it's
   in (`ssl.py` → Security; `sounddevice`/`_portaudio` → CoreAudio; `AppKit`/
   `Quartz` → window/Quartz).
3. **Get the C frame** (Python tracebacks stop at the boundary):
   ```
   lldb -- python scripts/macos_testbot.py ssl-race --iterations 20 --unsafe
   (lldb) run
   (lldb) bt all          # after it crashes
   ```
   or sample a live/hung process: `py-spy dump --pid <pid>`.
4. **Classify & fix.** Concurrent native init → serialize via
   `core.system.native_locks`. Off-main native call → route through
   `core.system.main_thread.run_on_main`. Then prewarm the handle once at startup
   so the first request isn't the first (racing) build.
5. **Lock it in.** Add a mocked, darwin-faked case to `tests/test_*` so the
   regression is caught off-Mac forever.
