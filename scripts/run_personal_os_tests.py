"""Run the personal OS verification suite for Wisp.

This is an orchestration script, not a pytest test. It runs the regular Python
suite, a Qt GUI smoke pass, and any platform-specific checks that only make
sense on the current operating system.

Typical use:

    python scripts/run_personal_os_tests.py
    python scripts/run_personal_os_tests.py --real-gui
    python scripts/run_personal_os_tests.py --deep-macos-native
"""
from __future__ import annotations

import argparse
import datetime as _dt
import os
from pathlib import Path
import platform
import subprocess
import sys
import time


REPO_ROOT = Path(__file__).resolve().parents[1]
LOG_ROOT = REPO_ROOT / "build_logs"


def _timestamp() -> str:
    return _dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def _safe_name(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in name)


def _run_step(
    name: str,
    args: list[str],
    *,
    log_dir: Path,
    env: dict[str, str] | None = None,
    timeout: int | None = None,
) -> int:
    print()
    print(f"== {name} ==")
    print("command:", " ".join(args))
    log_path = log_dir / f"{_safe_name(name)}.log"
    step_env = os.environ.copy()
    if env:
        step_env.update(env)

    with log_path.open("w", encoding="utf-8", errors="replace") as log:
        log.write(f"name={name}\n")
        log.write(f"command={' '.join(args)}\n\n")
        log.flush()
        started = time.monotonic()
        try:
            proc = subprocess.Popen(
                args,
                cwd=REPO_ROOT,
                env=step_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            assert proc.stdout is not None
            for line in proc.stdout:
                print(line, end="")
                log.write(line)
            status = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            status = 124
            msg = f"\nTIMEOUT: {name} exceeded {timeout}s\n"
            print(msg, end="")
            log.write(msg)
        elapsed = time.monotonic() - started
        log.write(f"\nexit_code={status}\nelapsed_seconds={elapsed:.1f}\n")

    if status == 0:
        print(f"PASS: {name}")
    else:
        print(f"FAILED: {name} (exit {status})")
        print(f"log: {log_path}")
    return status


def _gui_smoke(args: argparse.Namespace) -> int:
    screenshot_dir = Path(args.screenshot_dir).resolve()
    screenshot_dir.mkdir(parents=True, exist_ok=True)

    if args.mode == "offscreen":
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    sys.path.insert(0, str(REPO_ROOT))

    try:
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication
    except Exception as exc:
        print(f"SKIP: PySide6 is not available: {exc}")
        return 0

    app = QApplication.instance() or QApplication([])

    created = []

    def _dummy_send(_messages):
        yield "GUI smoke reply"

    def add_widget(name: str, widget, *, min_width: int = 360, min_height: int = 180) -> None:
        widget.setObjectName(f"personalOsSmoke_{name}")
        if widget.width() < min_width or widget.height() < min_height:
            widget.resize(max(widget.width(), min_width), max(widget.height(), min_height))
        widget.show()
        widget.raise_()
        app.processEvents()
        assert widget.width() > 0 and widget.height() > 0, f"{name} has invalid size"
        assert widget.isVisible(), f"{name} did not become visible"
        created.append((name, widget))

        screen = widget.screen() or QApplication.primaryScreen()
        if screen is not None:
            image = screen.grabWindow(widget.winId())
            if not image.isNull():
                out = screenshot_dir / f"{name}.png"
                image.save(str(out))
                print(f"captured {name}: {out}")

    try:
        from ui.agent.task_window import (
            AgentCommunicationDialog,
            AgentNudgeDialog,
            AgentTaskDialog,
        )
        from ui.bubble import SpeechBubble
        from ui.chat_window import ChatWindow
        from ui.intent_overlay import IntentOverlay
        from ui.settings_panel.dialog import SettingsDialog

        add_widget("settings", SettingsDialog(), min_width=540, min_height=420)
        add_widget("agent_task", AgentTaskDialog(), min_width=620, min_height=460)
        add_widget(
            "agent_communication",
            AgentCommunicationDialog(["Planner", "Builder"]),
            min_width=420,
            min_height=260,
        )
        add_widget(
            "agent_nudge",
            AgentNudgeDialog(["Planner", "Builder"]),
            min_width=420,
            min_height=260,
        )
        add_widget("intent_overlay", IntentOverlay(), min_width=360, min_height=180)
        chat = ChatWindow(
            [
                {
                    "messages": [
                        {"role": "user", "content": "Hello"},
                        {"role": "assistant", "content": "GUI smoke reply"},
                    ],
                    "context": "",
                }
            ],
            _dummy_send,
        )
        add_widget("chat", chat, min_width=620, min_height=440)
        bubble = SpeechBubble()
        bubble.append_chunk("GUI smoke bubble")
        add_widget("speech_bubble", bubble, min_width=240, min_height=80)

        if args.keep_open_seconds > 0:
            QTimer.singleShot(int(args.keep_open_seconds * 1000), app.quit)
            app.exec()
        else:
            app.processEvents()
    finally:
        for _name, widget in reversed(created):
            try:
                widget.close()
                widget.deleteLater()
            except RuntimeError:
                # Some widgets, such as popups with WA_DeleteOnClose, can be
                # destroyed by Qt before the smoke runner reaches cleanup.
                pass
        app.processEvents()

    print(f"GUI smoke passed ({args.mode})")
    return 0


def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--real-gui",
        action="store_true",
        help="Also show real desktop windows briefly and capture screenshots.",
    )
    parser.add_argument(
        "--keep-open-seconds",
        dest="real_keep_open_seconds",
        type=float,
        default=1.5,
        help="How long to keep real GUI windows open when --real-gui is used.",
    )
    parser.add_argument(
        "--deep-macos-native",
        action="store_true",
        help="On macOS, also run the native SSL race harness.",
    )
    parser.add_argument(
        "--pytest-args",
        nargs=argparse.REMAINDER,
        help="Extra args appended to the pytest command. Put this option last.",
    )
    parser.add_argument("--_gui-smoke", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--mode", choices=["offscreen", "real"], default="offscreen", help=argparse.SUPPRESS)
    parser.add_argument("--screenshot-dir", default="", help=argparse.SUPPRESS)
    parser.add_argument("--keep-open-seconds-internal", dest="keep_open_seconds", type=float, default=0.0, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args._gui_smoke:
        return _gui_smoke(args)

    run_id = _timestamp()
    log_dir = LOG_ROOT / f"personal_os_tests_{run_id}"
    log_dir.mkdir(parents=True, exist_ok=True)
    (LOG_ROOT / "latest_personal_os_tests.txt").write_text(str(log_dir), encoding="utf-8")

    print("Personal OS verification")
    print(f"repo: {REPO_ROOT}")
    print(f"os: {platform.platform()}")
    print(f"python: {sys.executable}")
    print(f"logs: {log_dir}")

    failures: list[str] = []

    pytest_cmd = [
        sys.executable,
        "-m",
        "pytest",
        "tests",
        "macos_py/brain/tests",
        "-q",
    ]
    if args.pytest_args:
        pytest_cmd.extend(args.pytest_args)
    if _run_step("pytest", pytest_cmd, log_dir=log_dir) != 0:
        failures.append("pytest")

    offscreen_screens = log_dir / "gui_offscreen"
    if _run_step(
        "gui-offscreen-smoke",
        [
            sys.executable,
            "scripts/run_personal_os_tests.py",
            "--_gui-smoke",
            "--mode",
            "offscreen",
            "--screenshot-dir",
            str(offscreen_screens),
        ],
        log_dir=log_dir,
        env={"QT_QPA_PLATFORM": "offscreen"},
    ) != 0:
        failures.append("gui-offscreen-smoke")

    if sys.platform == "darwin":
        if _run_step(
            "macos-smoke",
            [sys.executable, "scripts/macos_smoke.py"],
            log_dir=log_dir,
            env={"QT_QPA_PLATFORM": "offscreen"},
        ) != 0:
            failures.append("macos-smoke")
        if args.deep_macos_native:
            if _run_step(
                "macos-ssl-race",
                [
                    sys.executable,
                    "scripts/macos_testbot.py",
                    "ssl-race",
                    "--iterations",
                    "20",
                ],
                log_dir=log_dir,
                timeout=180,
            ) != 0:
                failures.append("macos-ssl-race")

    if args.real_gui:
        real_screens = log_dir / "gui_real"
        if _run_step(
            "gui-real-smoke",
            [
                sys.executable,
                "scripts/run_personal_os_tests.py",
                "--_gui-smoke",
                "--mode",
                "real",
                "--screenshot-dir",
                str(real_screens),
                "--keep-open-seconds-internal",
                str(args.real_keep_open_seconds),
            ],
            log_dir=log_dir,
        ) != 0:
            failures.append("gui-real-smoke")

    print()
    if failures:
        print("Personal OS verification failed:")
        for failure in failures:
            print(f"- {failure}")
        print(f"Logs written to: {log_dir}")
        return 1

    print("Personal OS verification passed.")
    print(f"Logs written to: {log_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
