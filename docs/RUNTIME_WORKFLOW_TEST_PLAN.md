# Runtime Workflow Test Plan

Snapshot: 2026-07-21

## Source documents

This plan must be implemented against the existing documents below rather than creating a separate function list:

- [Wisp App Function Inventory](./APP_FUNCTION_INVENTORY.md) is the authoritative list of **472 user-visible functions** and their **3,296 numbered failure causes**. Every inventory function must have a workflow-manifest record. Its numbered failure causes determine which fault scenarios should be applied to that function.
- [Test Suite Audit](./TEST_SUITE_AUDIT.md) is the baseline report for the current automated tests, test locations, execution groups, and known suite limitations. It should be updated when the new workflow suite changes how tests are collected or run.
- [Original App User Function Test Plan](../tests/APP_WORKFLOW_TEST_PLAN.md) is the earlier workflow catalogue and fixture design. Its existing real-store, fake-boundary, offscreen-Qt, and event-recorder approach should be reused rather than rebuilt.

The coverage marks currently recorded in `APP_FUNCTION_INVENTORY.md` describe direct failure assertions found in the old suite. They are a starting point, not proof that a real-use workflow exists. A function is counted toward the new `472 / 472` target only when its production entry point is exercised by an automated workflow under this plan.

The workflow manifest must preserve traceability back to the earlier inventory:

- Store the inventory function name exactly.
- Store every numbered failure reference assigned to that function.
- Map each applicable failure reference to a workflow scenario or record why it is not a runtime scenario.
- Never mark references under another function as covered merely because their wording is similar.
- Report missing inventory functions and unmapped failure references in CI.

## Implementation status

### Corrected feature-acceptance baseline

`tests/workflows/manifest.json` is now treated only as a traceability and test-candidate index. Its 472 records do **not** mean that 472 real-use functions work. The authoritative positive-behavior report is `tests/workflows/feature_acceptance.json`, generated from explicit audited overrides by `scripts/generate_feature_acceptance.py`.

Current honest status:

- **472 / 472** functions are accepted through a production entry point with a successful observable result.
- **0 / 472** have a name-matched candidate test that still needs code-path audit.
- **0 / 472** are untested at the real-entry acceptance level.
- **457 / 472** have completed dependency audits.
- **169 / 169** declared A -> B interaction matrices are accepted. These include the earlier launcher, Settings, appearance, platform, local-file, onboarding, and provider matrices, plus planned chunking, automatic elaboration, external transcript synchronization/ownership, every provider-control option, authentication, connection/model management, every supported provider route, Chat/Image/Memory row operations, route failover/cooldown, provider adaptation, capability warnings, conversation engine/owner/prompts, speech playback/provider/install/asset/STT/dictation/live-voice, durable-memory phrase/scope/retrieval/scheduler/editor, privacy mode/review/model/keychain, context-to-model-tool grants, local/add-on/MCP policy, live-file decisions, tool budgets, visible tool traces, add-on enablement/permissions/dependencies, MCP discovery/context relationships, agent model routing, agent-to-agent map membership, scope/role/parallel execution, permission decisions, live-run-to-history state, and manual/current-app task context composition.

The acceptance validator refuses to infer success coverage from section similarity, failure injection, or a shared internal helper. `tests/workflows/feature_interactions.json` separately records the behaviorally distinct state combinations for features that affect other features.

The first foundation slice is merged into the existing workflow system:

- `scripts/run_app_workflow_tests.py` remains the master workflow entry point.
- `scripts/runtime_test_harness.py` provides the shared escaped-exception collector, deterministic wait helper, and opt-in process/thread/JSON/temp-path inspector.
- The master runner now removes its completed named basetemps and collects abandoned `pytest_<pid>_*` trees only after confirming that their owner process is dead. Live concurrent runs and the explicit `WISP_KEEP_PYTEST_TEMP` debugging mode are preserved.
- Every `pytest.mark.workflow` test automatically uses the Python and Qt runtime-failure collector through `tests/conftest.py`.
- Real supervisor startup/shutdown and isolated app-state workflows use the state inspector first; it will be extended to more workflows as their continuing-resource allowlists are established.
- Existing `runtime.bootstrap` crash diagnostics are reused for worker processes, and the master runner now treats their `[crash] unhandled` marker as a test failure.
- Existing profile workflows are now included in the master runner.
- Settings appearance acceptance now runs real Save clicks through real config reload and the production UI-host callback into already-running icon and bubble widgets. Four compound appearance profiles cover every scroll x snap boolean state, low/high geometry and timing, all color roles, and successful use of the bubble after live apply.
- App-language acceptance saves every supported UI language and navigates all eight Settings pages after each translator change (48 cases). Assistant-language acceptance saves every offered state and verifies the exact prompt used by the runtime.
- Remaining Settings actions now exercise the real built-in-profile QAction, setup-check report, Settings-to-onboarding entry, every page-scoped reset, and the isolated full-reset path for settings, keychain entries, and OAuth sessions.
- Optional-installer acceptance now launches real child processes and drives success, resolver-failure, and cancellation outcomes through the visible Cancel, Copy log, Open log folder, Restart, and Close controls while verifying retained artifacts.
- About/diagnostics acceptance now covers installed-version display, every packaged-update check result, download/apply retry and cancellation, every repo-pull result, actual bounded/redacted crash ZIP contents, and the tray-to-supervisor-to-live-Runtime-Status window.
- Uninstall acceptance now builds both packaged and source plans with production safety validation, verifies every exact confirmation target, and executes the native self-removing helpers only against isolated temporary Wisp app/data/model trees.
- Cross-platform desktop acceptance now connects native-worker hotkey start/event/stop across Windows RegisterHotKey, Linux pynput, and the macOS Carbon helper; covers macOS AX, Wayland AT-SPI, X11 PRIMARY, and Windows UIA selection; runs every native paste method under both clipboard policies; drives all snip modes and live monitor/DPI translation; verifies real Qt window geometry/chrome; and clicks the log/report/task/add-on/conversation reveal controls before checking every file/folder x desktop command.
- First-run acceptance now enters the real wizard from the UI host, drives Back/Continue/Finish and the Settings re-entry button, covers every offered interface/assistant language and theme, every provider/model/endpoint/key route, ChatGPT sign-in precedence, all nine TTS x STT choices, the three-platform guidance x open-chat matrix, persisted runtime apply, local-installer scheduling, and suppression after setup completes.
- Launcher acceptance now executes the platform source launcher and a freshly rebuilt packaged executable. Both must show the real overlay, start four distinct worker processes, complete ping/flow/hotkey startup, shut down in-band, and leave no managed process; every release build runs the packaged smoke before creating its archive.
- Floating-shell acceptance now drives every icon state under both auto-hide settings, real Qt drag events, and the real tray visibility toggle. A four-worker UI-host workflow triggers the actual Last chat, Memory, Addon Manager, Settings, Runtime Status, and Quit actions, verifies their real target windows/process exit, and opens both ChatGPT and Claude provider controls. Windows, macOS, and Linux login entries are verified in enabled and disabled states against the proven source/packaged launch commands.
- Connection-management acceptance now drives the real Add connection modal for all 24 providers; crosses every provider/alias query with All, Cloud, and Local filters; verifies collapsed/expanded behavior; proves Save/Cancel and last/sibling removal keychain semantics; triggers all 21 endpoint menu actions; refreshes models for all 24 connection providers plus ChatGPT with typed and stored credentials; and sends exact manual models through local and remote Custom route probes. The audit fixed removal persistence so saving the final-row removal now clears its credential while Cancel remains non-destructive.
- Provider-runtime acceptance now selects every one of the 25 Chat providers as the primary route and clicks the visible Test Chat model button. Forty-seven executions cover typed and stored credentials for every keyed route plus ChatGPT OAuth, Copilot, and Ollama; each must reach the correct OpenAI-compatible endpoint, Anthropic Messages request, Codex Responses request, Copilot call, or Ollama readiness/client path with the exact selected model.
- Model-route acceptance now performs Add, remove, priority drop, per-row refresh, and route testing on Chat, Image, and Memory. Apply to all copies both ordered Chat rows into the other routes, and each route's visible Test button probes both primary and fallback with the correct image/route flags while preserving exact manual models.
- Model-runtime acceptance now drives three consecutive public `stream_response` turns: a transient primary failure falls through, the next turn skips the cooling route, and the recovered primary is retried after expiry. Existing production-adapter matrices also prove no-content and late-stream behavior, provider-specific streaming/tool/image/token/reasoning adaptation, and the complete capability-warning decision matrix through real Settings warning headers.
- Conversation-engine acceptance now saves all six Wisp/ChatGPT/Claude x Wisp/agent owner states through the real Settings UI. The Wisp/agent combination is correctly unavailable; all five valid states immediately drive the production brain handler, proving Wisp full-history handoff versus resumable agent sessions and isolated Wisp, Codex-developer, and Claude-system prompts.
- Speech acceptance now clicks the visible bubble stop and fast-forward controls, saves volume/normal/held speed and read-aloud chunk limits, and drives the audio worker across mute/attenuated/unity/amplified and chunk-boundary partitions. The visible Test TTS button runs Cartesia, ElevenLabs, OpenAI, OpenAI-compatible, GPT-SoVITS, and Kokoro auto/CPU/CUDA routes with exact field values. The real ElevenLabs and Kokoro install buttons now prove their package/settings plans, while Kokoro repair/update/cancel actions prove verified asset handling. This slice found and fixed both 0% file playback incorrectly falling back to 100% volume and completed Kokoro asset actions remaining stuck at “Downloading…” with a disabled button.
- STT acceptance now clicks the visible installer across all offered model, device, compute, language, and beam states; constructs all 60 model/device/compute runtime combinations; sends all 56 language/beam transcription requests; and saves custom background timing before running exact overlapping live windows. This slice found and fixed the installer persisting automatic language detection as the invalid explicit code `auto` instead of the empty/`None` runtime state.
- Dictation/live-voice acceptance now saves raw and LLM-cleanup dictation modes through the real Settings UI, proves both modes preserve the captured paste target, clicks the visible google-genai installer, and runs all 36 built-in/custom model x voice x duplex combinations through the production audio-session entry. Session tests cover toggle lifecycle, user/assistant transcript roles, full-duplex interruption, half-duplex microphone gating, and live-session exclusion of competing voice, dictation, reply-TTS, and read-aloud paths.
- Durable-memory acceptance now runs Remember that, Note that, and Keep in mind through complete brain-chat turns in both General and project scope; executes the opt-in model search/save tools and supported delete handler against a real isolated JSON store; saves automatic consolidation, interval, top-k, and STM budget through Settings before consuming them in scheduling, extraction, retrieval, and compression; and opens the real Long-term Memory viewer to add, edit, move, group, delete, and refresh facts through visible controls. This slice found and fixed successful background deletion leaving a stale visible row because its completion callback was scheduled from a thread without a Qt event loop.
- Privacy acceptance now saves Off, Built-in, and Advanced through the real Settings UI and proves the exact unmodified or placeholder-substituted text seen at the model boundary, including built-in-plus-local-AI merging and restored replies. The blocking brain review and real Qt sheet cover Send redacted, Send full message, and Cancel send, with Cancel proving no provider call. Visible install, repair, and remove actions reach the official isolated privacy-model plan and status-dependent fallback. Provider credentials save only through the OS-keychain boundary, disappear from plaintext Settings, and reopen as empty masked stored fields; Reset All dispatches settings and credential cleanup.
- Model-tool acceptance now executes web search, document/page retrieval, screen capture, durable-memory search/save, Git status/diff, and GitHub repository/issue adapters under their exact context grants. The real Allowed tools modal covers every local-file selector in Off/Auto, installed add-ons in Off/Auto, and all six MCP server x individual-tool states before passing saved policy into the supervisor. Live file writes cross worker IPC with a rendered diff and all Approve/Alternate/Decline outcomes; production tool-call/output budgets and visible persisted trace activity are also covered. A real isolated add-on host contributes and executes a model tool outside the app PID.
- Add-on acceptance now clicks the real archive/folder install, enable, Settings, Logs, and dependency-environment controls, pairing each UI route with real isolated installs, persistence, host processes, and repair state. Enabled/disabled and declared/missing permission matrices cover query/response hooks, tools, intent rows, hotkeys, tray actions, notifications, settings fields, and capped private auxiliary LLM calls. The MCP bridge now loads a real `servers.json`, handshakes with a child JSON-RPC server, discovers and executes its tools, and cleanly stops it; the external Wisp context server advertises and returns all five desktop context operations.
- Multi-agent acceptance now triggers both real tray actions; submits and copies a complete visible task spec; edits scope/globs/context/models/fallbacks/completion/parallel settings; adds, customizes, removes, pairs, and resets agents and communications; and drives live approvals, tabs, agent health/messages, card layout, pause/resume, nudge, cancel, diff/folder actions, retry/continue, and historical artifacts. Production runner tests pair those controls with role gates, provider routing, parallel read-only briefings, overlapping disjoint writes, and same-file lease exclusion. The newly visible Copy current app context control excludes Wisp's process, reuses the shared document/browser extraction and privacy redaction paths, applies a marked 12,000-character budget, preserves manual task context, and carries both into the runner prompt.
- Intent-action acceptance now opens both production pickers and executes What is this, Explain simply, How do I fix this, Fix grammar, Simplify, and Improve tone through both their assigned keys and their painted clickable rows, asserting the exact configured prompt in all twelve combinations.
- `tests/workflows/manifest.json` contains all **472 / 472** machine-readable trace/candidate mappings, validated by `tests/test_workflow_manifest.py` against exact inventory text, all **3,296 / 3,296** failure references, and real pytest node IDs. This is not positive feature acceptance coverage.
- `scripts/generate_workflow_manifest.py` reproducibly expands the inventory using curated test-family pools while preserving hand-verified records. It labels records as `verified`, `direct`, or broader `section` mappings so mapping completeness is not confused with direct workflow maturity.
- The master runner loads every manifest-referenced file dynamically, and pytest automatically applies the `workflow` marker and runtime collector to every mapped node.
- `tests/workflows/failure_coverage.json` separately records direct executable evidence for every numbered cause. It currently proves **3,296 / 3,296** references with **0** uncovered. Function mapping alone never grants coverage: shared harnesses are reused only when `tests/workflows/shared_failure_boundaries.json` declares the audited function/cause pairs and their exact executable nodes.
- The master runner has a separate exact-node failure-evidence phase, including evidence under `runtime/brain/tests`, so a documented `[T###]` cannot silently stop running.

The manifest now has `enforce_complete: true`. Missing functions, changed failure references, stale test node IDs, duplicate records, or a stale generated manifest fail the test suite.

Current trace-mapping maturity is **12 hand-verified**, **133 direct name-matched**, and **327 section-level** records. This proves every function is traceable into the automated suite; it does not replace the later plan phases that promote mappings to dedicated production-entry workflows.

The older mapping-maturity counts are retained as historical traceability information only. They must never be displayed as the current feature acceptance result.

## Feature dependency and interaction policy

The 472 functions are not tested as an indiscriminate 472 x 472 Cartesian product. An interaction is required when code-path tracing shows that feature A changes feature B's inputs, routing, state, availability, persistence, or visible result.

For every declared A -> B edge:

1. Partition A into every behaviorally distinct state.
2. Partition B into every relevant operating state.
3. Record every A-state x B-state case and its expected result in `tests/workflows/feature_interactions.json`.
4. Exercise the production entry point and successful result for every recorded case.
5. Use pairwise coverage when several independent features feed B; use the full Cartesian product when the production code contains a compound branch involving those features.
6. Mark both feature dependency audits complete only after their outgoing and incoming effects have been traced.

This policy covers cases such as Theme mode -> Save/reopen, active profile -> Settings persistence, context mode -> query payload, provider/model -> routing, and TTS mode -> reply playback without inventing combinations between unrelated functions.

## Objective

Build automated workflows that mimic real use of every user-visible Wisp function and expose runtime problems.

Primary coverage target: **472 / 472 functions executed through production behavior**.

The failure-cause catalogue in `APP_FUNCTION_INVENTORY.md` supplies scenarios to inject. It is not necessary to assert a useful error message for every cause.

## Meaning of workflow

A workflow is an automated process that performs the same sequence a user would perform, using the real application entry points and state transitions.

Examples:

- Launch Wisp, wait for its workers, open Settings, change a shortcut, save, restart, and verify the setting survived.
- Select text in a simulated external application, open an intent, submit it, stream a result, cancel it, and verify Wisp returns to idle.
- Open chat, attach a file, send a message, switch conversations, restart Wisp, and reopen the conversation.
- Start an optional installer, interrupt it, restart Wisp, and verify no broken package state or temporary files remain.

Mocks or fakes are allowed only at external boundaries that CI cannot safely control, such as provider servers, operating-system permissions, microphones, and keychains. The Wisp code between the user action and that boundary should remain real.

## What counts as a runtime failure

A workflow fails when it detects any of the following:

- An unhandled exception.
- An exception in a background thread, Qt callback, asynchronous task, worker, or subprocess.
- A native crash or non-zero worker exit that the workflow did not request.
- A deadlock, freeze, or operation exceeding its timeout.
- An application state that cannot return to idle or continue with another action.
- Corrupt settings, conversations, memory, task artifacts, or installer state.
- A leaked thread, worker, subprocess, file handle, lock, timer, or temporary directory.
- A stale callback or result changing a newer operation.
- A repeatable action failing on its second or later execution.
- A platform-specific failure on a supported operating system.

An expected provider, permission, validation, or filesystem rejection is acceptable when it stays controlled: the app remains alive, the operation terminates, state remains valid, and resources are cleaned up. Tests do not need to require a particular friendly log or recommendation.

## Required assertions for every workflow

Each workflow must verify:

1. The production entry point was invoked.
2. The action completed, was cancelled, or failed within a bounded timeout.
3. No unexpected exception or process exit was captured.
4. The app reached a valid state after the action.
5. A second unrelated action can still run.
6. Persistent data remains parseable and internally consistent.
7. Threads, workers, locks, timers, and temporary files return to the expected baseline.

## Workflow scenarios

Every function receives the scenarios that apply to it:

- **Normal:** Perform the action once with valid inputs.
- **Repeat:** Perform it multiple times and reopen any affected UI.
- **Cancel:** Cancel before start, during work, and near completion where possible.
- **Restart:** Restart the relevant worker or app and verify persisted state.
- **Boundary values:** Empty, minimum, maximum, oversized, malformed, and stale inputs.
- **Dependency failure:** Inject missing, corrupt, locked, denied, offline, timed-out, rate-limited, or crashed dependencies.
- **Concurrency:** Run conflicting or overlapping operations where the UI permits them.
- **Recovery:** After a controlled failure, run a valid operation and verify normal behavior resumes.
- **Cleanup:** Verify no Wisp-owned temporary files or orphan processes remain.

A parameterized scenario may cover many functions, but each function's actual entry point must be invoked. A helper-unit test alone does not count as workflow coverage for every caller.

## Test harness

### 1. Global runtime-failure collector

Add one collector used by all workflow tests. It must capture:

- Main-thread exceptions.
- `threading.excepthook` failures.
- `sys.unraisablehook` failures.
- Async task/future exceptions.
- Qt critical/fatal messages and exceptions escaping callbacks.
- Worker and subprocess exits, stderr tracebacks, and crash codes.
- Watchdog timeouts and UI event-loop freezes.

At workflow teardown, any unexpected collected event fails the test.

### 2. Application driver

Provide reusable commands for real user operations:

- Start and stop the supervisor and workers.
- Open, close, and interact with Wisp windows.
- Trigger tray actions, shortcuts, intent actions, chat actions, and Settings controls.
- Wait for explicit state transitions instead of arbitrary sleeps.
- Restart workers or the full app while retaining the test profile.

### 3. Fault injection

Add deterministic boundary adapters for:

- Filesystem: missing, read-only, locked, corrupt, full, interrupted write.
- Network/provider: offline, timeout, authentication failure, rate limit, invalid response, disconnect during streaming.
- Workers: missing executable, failed startup, crash, freeze, delayed reply, failed shutdown.
- Native OS: denied accessibility, screen-recording, microphone, clipboard, keychain, and global-hotkey access.
- Audio/model assets: missing, damaged, unsupported device, no audio, unavailable model.
- Add-ons/MCP/agents: invalid manifest, missing dependency, denied permission, malformed protocol data, host crash, lease conflict.

Faults must be injected at the external boundary, not by replacing the Wisp function being tested.

### 4. State and leak inspector

Record a baseline before each workflow and compare it at teardown:

- Wisp worker and helper processes.
- Non-daemon threads.
- Registered hotkeys and active timers.
- Held locks and open temporary work areas.
- Settings, conversation, memory, add-on, and task JSON validity.
- Wisp-owned pytest and installer temporary directories.

The inspector may allow resources explicitly owned by the continuing app session, but the allowlist must be narrow and documented.

## Test layers

Use all layers; lower layers run more frequently.

1. **In-process workflows:** Real controllers, stores, and UI objects with external boundaries simulated. Run on every change.
2. **Multi-worker workflows:** Real supervisor IPC and worker processes. Run on every change for critical flows and in a broader CI job.
3. **Desktop workflows:** Real Qt event loop, windows, shortcuts, clipboard, capture, and cancellation behavior. Run in supported CI desktop sessions.
4. **Packaged-app workflows:** Launch the built executable and exercise startup, update, crash recovery, and uninstall boundaries. Run for release candidates.
5. **Stress workflows:** Randomized valid action sequences, repeated start/stop cycles, concurrency, and delayed callbacks. Run nightly or on demand.

## Workflow manifest

Create a machine-readable manifest, proposed path: `tests/workflows/manifest.yaml`.

Each of the 472 functions needs one record containing:

- Stable function/reference identifier.
- Human-readable function name.
- Workflow test node IDs.
- Production entry point exercised.
- Applicable scenario families.
- Supported operating systems.
- Required optional components.
- Maximum allowed duration.
- Expected persistent-state changes.
- Expected continuing resources and cleanup requirements.

The manifest validator must fail when:

- An inventory function has no workflow.
- A referenced test node does not exist.
- A workflow is permanently skipped on every supported platform.
- A workflow does not use the runtime-failure collector and cleanup inspector.

## Implementation phases

### Phase 1: Foundation

- Add the global runtime-failure collector.
- Add deterministic waits and watchdogs.
- Add the state/leak inspector.
- Reuse the existing pytest temporary-directory cleanup.
- Add the workflow manifest and validator.

Exit condition: one small workflow can deliberately produce each collector failure and prove the harness catches it.

### Phase 2: Critical end-to-end slice

Implement workflows for:

- App launch and clean shutdown.
- Worker startup, crash, freeze, restart, and failed shutdown.
- Main ask and rewrite actions.
- Streaming and cancellation.
- Chat send, history persistence, corruption recovery, and restart.
- Settings change, save, discard, and restart.

Exit condition: critical workflows run through real supervisor/UI/brain/audio boundaries without unexpected failures or leftovers.

### Phase 3: High-risk boundaries

Implement reusable scenario matrices for:

- Filesystem and persistence.
- Provider/model routing.
- Native permissions and hotkeys.
- Screen capture and context collection.
- TTS, STT, dictation, and live voice.
- Add-ons, MCP, and local-file tools.
- Multi-agent tasks and file leases.

Exit condition: every relevant function is executed against its applicable boundary-failure matrix.

### Phase 4: Remaining function inventory

Work through all 20 inventory sections until the manifest reaches **472 / 472**.

For each section:

1. Add normal workflows.
2. Add repeat, cancel, and restart cases.
3. Attach applicable fault matrices.
4. Run the section repeatedly.
5. Fix discovered runtime issues before marking the section complete.

### Phase 5: Platform and packaged validation

- Run Windows, macOS, and Linux jobs for platform-specific workflows.
- Run packaged startup and shutdown smoke tests.
- Exercise optional-component combinations separately.
- Run release-candidate update and uninstall workflows in disposable environments.

Exit condition: no supported platform has an uncovered platform-specific function.

### Phase 6: CI enforcement

Add required CI gates for:

- `472 / 472` inventory functions mapped to existing workflows.
- Zero unexpected runtime-failure events.
- Zero workflow timeouts.
- Zero invalid persistent stores after teardown.
- Zero unexpected worker/process/thread/temp-file leaks.
- All mandatory platform jobs passing.

Keep long stress and packaged tests in separate jobs so the fast workflow suite remains practical for normal development.

## Recommended work order

Prioritize by damage and likelihood:

1. Startup, shutdown, IPC, and worker lifecycle.
2. Ask/rewrite/chat streaming and cancellation.
3. Settings, conversations, memory, and other persistent stores.
4. Provider routing, authentication, and tool execution.
5. Native hotkeys, focus, clipboard, capture, and permissions.
6. TTS, STT, dictation, and live voice.
7. Add-ons, MCP, and multi-agent execution.
8. Installers, updates, crash reports, and uninstall.
9. Remaining visual and convenience controls.

## Completion definition

This plan is complete only when:

- Every one of the 472 inventory functions is executed by at least one automated real-use workflow.
- Applicable normal, repeat, cancel, restart, boundary, fault, recovery, and cleanup scenarios are present.
- All workflows use the shared runtime-failure collector and state/leak inspector.
- Supported operating systems execute their platform-specific workflows.
- CI reports zero unexpected crashes, exceptions, hangs, corrupt stores, leaked resources, and Wisp-owned temporary leftovers.

The number of tests is not the success metric. The success metric is complete function execution with broad runtime fault exposure and zero undetected runtime failures.
