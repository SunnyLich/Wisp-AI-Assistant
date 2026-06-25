# Unified Chat Tool Harness

Wisp now uses the provider-neutral chat tool loop as the single chat-tool
runtime. The old current-vs-unified comparison harness and OpenAI Evals package
adapter have been removed.

## Runtime

`ChatToolLoop` owns the shared observe-act-observe behavior:

- tool rounds and tool-call budgets
- normalized `WispToolCall` and `WispToolResult` records
- observations sent back to the provider adapter
- completion-gate nudges
- progress chunks and final trace metadata

Provider adapters translate their native protocol into the shared loop shape.
The ChatGPT/Responses and Anthropic chat paths route tool use through the
unified loop directly.

## Harness

The local harness lives in `core/llm_clients/chat_flow_harness.py`. It runs only
the unified flow:

```text
ChatScenario
  prompt, tools, permissions, expected checkpoints

ChatFlowRunner
  scripted or live unified runner

run_chat_flow_harness(...)
  normalized trace, score, summary, artifacts
```

The local deterministic grader lives in `core/llm_clients/harness_grading.py`.
It checks expected tool names, expected tool arguments, recovery after failed
tools, completion-gate misses, and final-answer evidence.

## Scripts

Use:

```text
python scripts/run_unified_chat_harness.py
python scripts/benchmark_unified_tool_reliability.py
```

The harness writes:

```text
chat_flow_harness_runs/<timestamp>/
  summary.json
  scenarios.json
  harness_spec.json
  harness_scores.json
  results.json
  traces/
    <scenario>.json
  report.md
  report.html
```

Live real-tool runs still require deliberate opt-in because they can send
workspace-derived content to a model provider.
