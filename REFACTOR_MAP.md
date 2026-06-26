# Refactor Map — value vs. risk, no edits yet

_Generated as an assessment pass. Nothing in the codebase was changed to produce this._

## The honest headline

- **~74k LOC source + ~30k LOC tests.** This is **not** mostly disposable patches.
  Most of it is real feature surface: multi-provider LLM client, overlay UI, addon
  system, agent runner, ambient context, TTS/STT, 3-OS packaging.
- **Dead code is nearly nonexistent.** A whole-repo scan found exactly **one** orphaned
  module: `core/system/app_platform.py` (16 lines). `addon_host.py` and `brain_host.py`
  *look* unused but are launched as subprocesses by string name — they are live.
- The bloat you're sensing is **real but concentrated** in two textures:
  1. **God-files** doing many unrelated jobs in one module.
  2. **Defensive cruft** — **382 catch-all `except`** + **141 silent `except: pass`**
     in non-test code. These hide bugs *and* drown readability.
- **"Without side-effect" is achievable only incrementally.** A 74k-LOC rewrite cannot
  be side-effect-free. Decomposition that *moves* code without changing behavior, each
  step gated by the existing test suite, can be. You already proved this pattern works
  by unifying the tool loop into `ChatToolLoop`.

## The god-files (size = navigation tax, not necessarily deletable LOC)

| File | LOC | What's crammed in | Catch-all / silent except |
|---|---|---|---|
| `ui/settings_panel/dialog.py` | 6,990 | every settings section in one dialog | 17 / 9 |
| `core/llm_clients/client.py` | 5,906 | tool *execution* + per-provider schemas ×3 + model-quirk flags + route failover + PDF/doc reading + streaming clients | 9 / — |
| `runtime/supervisor/flows.py` | 4,300 | one `FlowController` class, dozens of tiny `_on_*` IPC delegators | 28 / — |
| `runtime/workers/ui_host.py` | 4,104 | UI worker, mixed responsibilities | 22 / — |
| `ui/agent/task_window.py` | 3,601 | agent task window | — |
| `core/context_fetcher.py` | 3,467 | window + clipboard + browser + search + HTML parsing + fs-watcher | 67 / 28 |
| `core/agent/runner.py` | 2,640 | agent execution loop | — |
| `runtime/brain/wisp_brain/handlers.py` | 2,508 | brain-worker method handlers | 15 / — |

## Prioritized work (value · risk · effort)

### Tier 1 — high value, low risk, do first

1. **Delete `core/system/app_platform.py`** (16 LOC, zero references).
   _Value: tiny. Risk: ~none. Effort: minutes._ Pure win, good warm-up.

2. **Consolidate defensive `try/except` behind a `safe()` helper, starting in
   `context_fetcher.py`** (67 catch-alls / 28 silent passes there alone).
   Replace the repeated `try: ... except Exception: pass` with one small helper
   (`safe(fn, default=..., log=...)`) so failures are logged once, consistently,
   instead of silently swallowed. This *reduces real LOC* and makes the app
   debuggable.
   _Value: high (readability + debuggability). Risk: low. Effort: medium._

3. **Decompose `core/llm_clients/client.py` into a package** — move (don't rewrite):
   - `tool_execution.py` — the `_execute_*` tool bodies
   - `provider_schemas.py` — `_get_tool_schemas` / `_get_openai_tool_schemas` / `_get_responses_tool_schemas`
   - `model_quirks.py` — `_model_accepts_images`, `_apply_sampling`, `_apply_max_output`, etc.
   - `routing.py` — route cooldown / failover (`_route_*`, `_is_quota_error`, …)
   - `documents.py` — PDF / document reading
   - `client.py` — keeps only the streaming client surface
   _Value: very high (biggest readability win, it's the heart of the app).
   Risk: low if done as mechanical moves + re-exports, gated by the LLM tests.
   Effort: medium-high._

### Tier 2 — high value, medium risk

4. **Split `flows.py` `FlowController`** into domain handler modules/mixins
   (hotkey, snip, memory, settings, addons, agent, audio). The `_on_*` methods are
   highly regular — many are 4-line forwarders, ideal for a dispatch-table or mixin
   split. _You already have `FLOWS_SPLIT_PLAN.md` for this._
   _Value: high. Risk: medium (live IPC router). Effort: medium._

5. **Decompose `settings_panel/dialog.py`** (6,990) into one module per settings
   section. Largest file in the repo; almost certainly the easiest to split cleanly
   because sections are independent.
   _Value: high. Risk: low-medium. Effort: medium-high._

6. **Split `core/context_fetcher.py`** into a package (window / browser / search /
   page-parsing / fs-watcher), combined with the `safe()` helper from item 2. The
   per-OS branches are already explicit, so the seams are clean.
   _Value: high. Risk: medium. Effort: medium._

### Tier 3 — medium value, cleanup

7. **Fix docstring drift** — several files still name old paths (`core/llm.py`,
   `core/agent_runner.py`). Cheap correctness-of-docs win.
8. **Close out the open `*_PLAN.md` debt** — `AUTO_AGENT_TOOL_CAPABILITY_PLAN`,
   `TEMP_PROFILE_PLAN`, `FLOWS_SPLIT_PLAN`, `CHAT_TOOL_LOOP_COMPARISON_PLAN`. Each is
   an unfinished or finished-but-not-removed refactor marker. Finish or delete.
9. **Audit `ui_host.py` (4,104) and `task_window.py` (3,601)** for the same
   decomposition treatment once the patterns above are proven.

## Realistic expectations

- **Total LOC won't collapse.** Expect roughly **10–20% reduction**, concentrated in
  the defensive cruft (items 1, 2) — the rest is decomposition that keeps LOC roughly
  flat but makes the code navigable.
- **The real prize is readability and debuggability**, not a smaller line count.
- **Every step must be test-gated.** Run the relevant suite before/after each move;
  commit per logical step so any regression is bisectable.

## Suggested order

`1 → 2 (context_fetcher only) → 3 → run full suite → 4/5/6 in whichever you feel most → 7/8`
