"""Run scripted or live unified chat-flow harness checks."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.llm_clients.chat_flow_harness import (  # noqa: E402
    ChatScenario,
    ScriptedModelStep,
    UnifiedScriptedChatFlowRunner,
    live_chatgpt_runner,
    run_chat_flow_harness,
    sample_harness_self_test_scenarios,
    synthetic_live_scenarios,
    write_harness_artifacts,
)
from core.llm_clients.chat_tool_loop import WispToolCall  # noqa: E402


def main() -> int:
    """Run the unified harness self-test or live smoke run and write artifacts."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-root",
        default="chat_flow_harness_runs",
        help="Folder where harness artifacts should be written.",
    )
    parser.add_argument(
        "--live-chatgpt",
        action="store_true",
        help="Run a real ChatGPT/Responses smoke run instead of the scripted harness self-test.",
    )
    parser.add_argument(
        "--real-tools",
        action="store_true",
        help="With --live-chatgpt, execute real local Wisp tools instead of synthetic safe fixtures.",
    )
    parser.add_argument(
        "--model",
        default="",
        help="Optional ChatGPT model override for --live-chatgpt.",
    )
    parser.add_argument(
        "--serial",
        action="store_true",
        help="Run scenarios serially instead of the default parallel harness mode.",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=0,
        help="Maximum parallel workers. Defaults to min(8, scenarios).",
    )
    args = parser.parse_args()

    if args.live_chatgpt:
        if args.real_tools:
            scenarios = [
                ChatScenario(
                    name="live_file_context",
                    prompt="What does this project use for settings storage? Use the available file tools if needed.",
                    tools=["list_files", "read_file"],
                    expected_relevant_tools=["list_files", "read_file"],
                ),
                ChatScenario(
                    name="live_memory",
                    prompt="What do you remember about how I like answers?",
                    tools=["memory_search"],
                    expected_relevant_tools=["memory_search"],
                ),
            ]
        else:
            scenarios = synthetic_live_scenarios()
        runner = live_chatgpt_runner(args.model or None, synthetic_tools=not args.real_tools)
        report = run_chat_flow_harness(
            scenarios,
            runner,
            parallel=not args.serial,
            max_workers=args.max_workers or None,
        )
        run_dir = write_harness_artifacts(report, args.output_root, report_title="Live Unified Chat Flow Smoke Run")
        print(f"Wrote live unified chat-flow artifacts to {run_dir}")
        print(f"Consolidated results: {run_dir / 'results.json'}")
        return 0

    scenarios = sample_harness_self_test_scenarios()
    runner = UnifiedScriptedChatFlowRunner(
        "unified",
        {
            "needs_file_context": [
                ScriptedModelStep(tool_calls=[WispToolCall(id="list_1", name="list_files")]),
                ScriptedModelStep(
                    tool_calls=[WispToolCall(id="read_1", name="read_file", arguments={"path": "config.py"})]
                ),
                ScriptedModelStep(final="Settings storage is defined from config.py.", status="handled"),
            ],
            "edit_plus_verification": [
                ScriptedModelStep(
                    tool_calls=[WispToolCall(id="read_1", name="read_file", arguments={"path": "app.py"})]
                ),
                ScriptedModelStep(
                    tool_calls=[
                        WispToolCall(
                            id="edit_1",
                            name="edit_file",
                            arguments={"path": "app.py", "old": "bad", "new": "good"},
                        )
                    ]
                ),
                ScriptedModelStep(
                    tool_calls=[
                        WispToolCall(
                            id="verify_1",
                            name="run_command",
                            arguments={"args": ["python", "-m", "py_compile", "app.py"]},
                        )
                    ]
                ),
                ScriptedModelStep(final="Fixed app.py and verified it.", status="handled"),
            ],
            "permission_boundary": [
                ScriptedModelStep(final="Delete is disabled by permissions.", status="blocked"),
            ],
        },
    )

    report = run_chat_flow_harness(
        scenarios,
        runner,
        parallel=not args.serial,
        max_workers=args.max_workers or None,
    )
    run_dir = write_harness_artifacts(report, args.output_root, report_title="Scripted Unified Harness Self-Test")
    print(f"Wrote scripted unified harness artifacts to {run_dir}")
    print(f"Consolidated results: {run_dir / 'results.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
