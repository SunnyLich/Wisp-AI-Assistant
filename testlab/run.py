"""Testlab orchestrator: run the automated release-gate checks for this OS.

    python testlab/run.py                      # release tier (default)
    python testlab/run.py --tier smoke         # quick pass
    python testlab/run.py --tier deep          # + fresh install checks (big downloads)
    python testlab/run.py --only llm_query     # one check
    python testlab/run.py --list               # show the plan for this OS

Each check runs in its own subprocess with a timeout; a native crash or hang in
one check cannot take down the run. Reports: testlab/reports/<timestamp>/.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

TESTLAB_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTLAB_DIR.parent
sys.path.insert(0, str(TESTLAB_DIR))

import _lab  # noqa: E402

TIERS = ("smoke", "release", "deep")


@dataclass
class Check:
    """One registered check script."""

    name: str
    script: str                       # relative to testlab/
    tier: str                         # lowest tier that includes it
    timeout: float
    platforms: tuple[str, ...] = ()   # empty = all platforms
    needs_display: bool = False       # skipped with --no-display
    spends_tokens: bool = False       # skipped with --no-spend
    args: tuple[str, ...] = ()

    def tiers(self) -> set[str]:
        return set(TIERS[TIERS.index(self.tier):])


# Registered checks. Implemented one at a time; run.py warns about entries
# whose script does not exist yet instead of failing the whole run.
CHECKS: list[Check] = [
    Check("app_boot", "checks/app_boot.py", "smoke", timeout=420),
    Check("llm_query", "checks/llm_query.py", "smoke", timeout=300, spends_tokens=True),
    Check("tts_function", "checks/tts_function.py", "smoke", timeout=600),
    Check("stt_roundtrip", "checks/stt_roundtrip.py", "smoke", timeout=600),
    Check("hotkeys", "checks/hotkeys.py", "smoke", timeout=120),
    Check("gui_smoke", "checks/gui_smoke.py", "smoke", timeout=300),
    Check("flow_e2e", "checks/flow_e2e.py", "release", timeout=600, spends_tokens=True),
    Check("macos_native", "checks/macos_native.py", "release", timeout=600, platforms=("darwin",)),
    Check("install_stt", "checks/install_stt.py", "deep", timeout=1800),
    Check("install_tts", "checks/install_tts.py", "deep", timeout=3600),
]


@dataclass
class Outcome:
    """Result of running one check."""

    check: Check
    status: str          # pass / fail / skip / timeout / error / missing
    seconds: float
    detail: str
    log_path: Path | None = None
    extra: dict = field(default_factory=dict)


def _selected(args: argparse.Namespace) -> list[tuple[Check, str]]:
    """Return (check, skip_reason) pairs; empty reason means run it."""
    only = {name.strip() for name in (args.only or "").split(",") if name.strip()}
    skip = {name.strip() for name in (args.skip or "").split(",") if name.strip()}
    rows: list[tuple[Check, str]] = []
    for check in CHECKS:
        if only and check.name not in only:
            continue
        reason = ""
        if not only and args.tier not in check.tiers():
            continue
        if check.name in skip:
            reason = "skipped via --skip"
        elif check.platforms and sys.platform not in check.platforms:
            reason = f"not for this platform ({sys.platform})"
        elif args.no_spend and check.spends_tokens:
            reason = "skipped via --no-spend"
        elif not (TESTLAB_DIR / check.script).exists():
            reason = "not implemented yet"
        rows.append((check, reason))
    return rows


def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill a check process and its worker children."""
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
        )
    else:
        import signal

        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            proc.kill()
    try:
        proc.wait(timeout=10)
    except Exception:
        pass


def _run_check(check: Check, report_dir: Path, timeout_scale: float) -> Outcome:
    log_path = report_dir / f"{check.name}.log"
    timeout = check.timeout * timeout_scale
    cmd = [sys.executable, str(TESTLAB_DIR / check.script), *check.args]
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONFAULTHANDLER"] = "1"
    env.setdefault("PYTHONIOENCODING", "utf-8")
    popen_kwargs: dict = {}
    if sys.platform != "win32":
        popen_kwargs["start_new_session"] = True

    print(f"\n== {check.name} == (timeout {int(timeout)}s)")
    print("   " + " ".join(cmd))
    started = time.monotonic()
    lines: list[str] = []
    with log_path.open("w", encoding="utf-8", errors="replace") as log:
        proc = subprocess.Popen(
            cmd,
            cwd=REPO_ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            **popen_kwargs,
        )
        assert proc.stdout is not None
        deadline = started + timeout
        timed_out = False
        import threading

        def _pump() -> None:
            for line in proc.stdout:  # type: ignore[union-attr]
                lines.append(line)
                log.write(line)
                log.flush()
                try:
                    print("   | " + line.rstrip())
                except UnicodeEncodeError:
                    pass

        pump = threading.Thread(target=_pump, daemon=True)
        pump.start()
        while proc.poll() is None:
            if time.monotonic() > deadline:
                timed_out = True
                _kill_tree(proc)
                break
            time.sleep(0.5)
        pump.join(timeout=10)
        returncode = proc.poll()

    seconds = round(time.monotonic() - started, 1)
    output = "".join(lines)
    if timed_out:
        return Outcome(check, "timeout", seconds, f"no result after {int(timeout)}s (process tree killed)", log_path)
    parsed = _lab.parse_result(output)
    if parsed is None:
        tail = "".join(lines[-15:]).strip()
        return Outcome(
            check,
            "error",
            seconds,
            f"exited {returncode} without a LAB_RESULT line (crash?). Tail:\n{tail}",
            log_path,
        )
    status = str(parsed.get("status") or "error")
    if status == "pass" and returncode != 0:
        # A crash after the result line (e.g. native teardown fault) is a finding.
        status = "error"
        parsed["detail"] = f"reported pass but exited {returncode} - teardown crash? ({parsed.get('detail')})"
    return Outcome(check, status, seconds, str(parsed.get("detail") or ""), log_path, dict(parsed.get("extra") or {}))


def _write_reports(report_dir: Path, outcomes: list[Outcome], tier: str, seconds: float) -> None:
    data = {
        "tier": tier,
        "platform": sys.platform,
        "python": sys.version.split()[0],
        "started": report_dir.name,
        "seconds": round(seconds, 1),
        "results": [
            {
                "name": outcome.check.name,
                "status": outcome.status,
                "seconds": outcome.seconds,
                "detail": outcome.detail,
                "log": str(outcome.log_path) if outcome.log_path else "",
                "extra": outcome.extra,
            }
            for outcome in outcomes
        ],
    }
    (report_dir / "report.json").write_text(json.dumps(data, indent=2), encoding="utf-8")

    bad = [o for o in outcomes if o.status in ("fail", "timeout", "error")]
    lines = [
        f"# Testlab report - {report_dir.name}",
        "",
        f"- platform: `{sys.platform}` | tier: `{tier}` | total: {round(seconds, 1)}s",
        f"- verdict: **{'FAIL' if bad else 'PASS'}**"
        + (f" ({len(bad)} of {len(outcomes)} checks failed)" if bad else ""),
        "",
        "| check | status | time | detail |",
        "|---|---|---|---|",
    ]
    for outcome in outcomes:
        detail = outcome.detail.replace("\n", " ")[:200].replace("|", "\\|")
        lines.append(
            f"| {outcome.check.name} | {outcome.status.upper()} | {outcome.seconds}s | {detail} |"
        )
    (report_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tier", choices=TIERS, default="release")
    parser.add_argument("--only", default="", help="comma-separated check names (ignores tier)")
    parser.add_argument("--skip", default="", help="comma-separated check names to skip")
    parser.add_argument("--no-spend", action="store_true", help="skip checks that spend real API tokens")
    parser.add_argument("--timeout-scale", type=float, default=1.0, help="multiply every check timeout")
    parser.add_argument("--list", action="store_true", help="show the plan for this OS and exit")
    args = parser.parse_args(argv)

    rows = _selected(args)
    if args.list:
        print(f"testlab checks on {sys.platform} (tier {args.tier}):")
        for check, reason in rows:
            state = reason or "will run"
            print(f"  {check.name:<14} tier>={check.tier:<8} {state}")
        return 0
    if not rows:
        print("no checks selected")
        return 2

    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    report_dir = TESTLAB_DIR / "reports" / stamp
    report_dir.mkdir(parents=True, exist_ok=True)
    (TESTLAB_DIR / "reports" / "latest.txt").write_text(str(report_dir), encoding="utf-8")

    print("Wisp testlab")
    print(f"  platform: {sys.platform}  tier: {args.tier}")
    print(f"  reports:  {report_dir}")

    outcomes: list[Outcome] = []
    started = time.monotonic()
    for check, reason in rows:
        if reason:
            print(f"\n== {check.name} == SKIP ({reason})")
            outcomes.append(Outcome(check, "skip", 0.0, reason))
            continue
        outcomes.append(_run_check(check, report_dir, args.timeout_scale))
    total = time.monotonic() - started

    _write_reports(report_dir, outcomes, args.tier, total)
    print(f"\n{'=' * 60}")
    width = max(len(o.check.name) for o in outcomes)
    for outcome in outcomes:
        print(f"  {outcome.check.name:<{width}}  {outcome.status.upper():<8} {outcome.seconds:>7}s  {outcome.detail.splitlines()[0][:90] if outcome.detail else ''}")
    bad = [o for o in outcomes if o.status in ("fail", "timeout", "error")]
    print(f"\n  {'FAIL' if bad else 'PASS'} - report: {report_dir / 'report.md'}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
