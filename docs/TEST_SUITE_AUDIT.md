# Test Suite Organization Audit

> **Status update (2026-07-22):** This document is the pre-reorganization
> baseline. The active source of truth is now
> [`tests/TEST_MAP.md`](../tests/TEST_MAP.md). All 167 pytest files / 2,608 tests
> are under the single `tests/` root; the former brain suite is now
> `tests/integration/brain`, infrastructure tests are in `tests/support`, and
> catalogue validators are in `tests/catalog`. Every test is classified as
> internal/user-path and GitHub-safe/isolated-host. The historical findings
> below are retained to explain why those changes were made.

**Repository:** Wisp (`Python-AI-assistant-overlay`)
**Audit date:** 2026-07-20
**Scope:** Test layout, discovery, configuration, runners, CI selection, release checks, and generated test artifacts
**Method:** Read-only repository inspection plus pytest collection; no full test execution was performed

## Executive summary

The test code is not literally scattered without structure: most pytest tests live under `tests/`, the headless brain has a colocated suite under `tests/integration/brain/`, and real-system release checks intentionally live under `testlab/`. The larger problem is that the repository does not have one authoritative definition of "the full suite."

The current working tree has four distinct testing systems:

1. The default pytest suite under `tests/`.
2. A separate headless-brain pytest suite under `tests/integration/brain/`.
3. A bespoke real-system release gate under `testlab/`.
4. Manual diagnostic programs and orchestration scripts under `tools/` and `scripts/`.

Each system is individually understandable. Together, however, they create inconsistent coverage depending on which documented command or CI runner is used. The highest-priority concern is therefore **suite-boundary drift**, not the raw number of directories.

Key findings:

- The default command collects **1,919 tests from 134 files under `tests/`** in the current working tree.
- The brain suite contributes another **141 tests from 12 files**, but is excluded from default pytest discovery and the primary CI chunks.
- The macOS runner and personal OS runner explicitly include the brain suite, so local and platform-specific definitions of "full" differ from primary CI.
- Pytest configuration is duplicated in `pytest.ini` and `pyproject.toml`. Pytest reports that it is using `pytest.ini` and ignoring the pyproject pytest configuration.
- Primary CI distributes files evenly, but not tests or runtime. Current chunks contain **405, 726, 487, and 282 selected test cases**, respectively.
- Several test modules have become very large; the two largest exceed 5,000 physical lines each.
- Execution-level taxonomy is weak. Unit, UI, workflow, platform, real-host, and integration behavior are mixed in the same broad tree, while only exceptional lanes receive markers.
- Thousands of ignored generated files make the checkout look more disorganized than the tracked source actually is.

The recommended strategy is incremental: first establish a single suite contract and configuration, then make CI and documentation use it, then reorganize and split files without changing test behavior.

## Snapshot caveat

This audit describes the **current filesystem and working tree**, not only the last committed revision. At audit time:

- 139 pytest-shaped or `test_*` Python files were tracked across `tests/`, `tests/integration/brain/`, and `tools/`.
- 10 additional test files under `tests/` were present but untracked.
- Many existing source and test files were modified.

Consequently, collection totals and line counts below describe the code that a developer would run locally now. They should not be interpreted as metrics for the current Git `HEAD` alone.

## Current inventory

### Pytest and test-named Python files

| Area | Files on disk | Physical lines | Observed role | Included by default pytest? |
|---|---:|---:|---|---|
| `tests/*.py` | 117 | 40,543 | Core, UI, workflows, platform, tooling, and integrations | Yes |
| `tests/runtime/*.py` | 17 | 11,843 | Supervisor, worker hosts, IPC, runtime flows | Yes |
| `tests/integration/brain/*.py` | 12 | 3,567 | Brain host and request-handler behavior | No |
| `tools/test_*.py` | 3 | 400 | Manual diagnostics or packaged-app probes | No |

The two `tests/` rows form the current default collection boundary: 134 files and 52,386 physical lines.

The three files under `tools/` look like pytest modules by name but contain no discovered `test_*` functions or `Test*` classes. Their names describe what the utility tests, not that pytest owns them. Pytest's configured `testpaths` prevents accidental default collection, but the naming still creates navigational ambiguity.

### Current collection results

Collection was measured with the repository virtual environment and cache writing disabled:

```powershell
.\.venv\Scripts\python.exe -m pytest --collect-only -q -p no:cacheprovider
.\.venv\Scripts\python.exe -m pytest tests/integration/brain --collect-only -q -p no:cacheprovider
```

Results:

| Selection | Collected tests |
|---|---:|
| Default `tests/` suite | 1,919 |
| Separate brain suite | 141 |
| Combined conceptual total | 2,060 |

The combined total is conceptual because no repository-wide canonical command currently declares both directories to be one suite.

### Marker selections

`pytest.ini` registers four project-specific markers. Their current collection sizes are:

| Marker | Selected tests | Intended role |
|---|---:|---|
| `workflow` | 102 | User-visible workflows and selected UI contracts |
| `real_host` | 10 | Real desktop, clipboard, screenshot, tray, or input APIs |
| `real_gpt55` | 2 | Token-spending live model calls |
| `real_harness` | 2 | Token-spending Codex/Claude harness calls |

These counts should not be added together because a test may participate in more than one conceptual lane. More importantly, there are no registered `unit`, `integration`, `ui`, `platform`, `brain`, or `slow` markers. Most of the suite is therefore classified by filename, directory, dependency availability, or runner allowlist rather than by a consistent execution-level taxonomy.

## Test execution topology

### 1. Documented default command

`docs/DEVELOPER_README.md` calls the following command the full suite:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Because both active pytest configurations specify `testpaths = tests`, this command excludes `tests/integration/brain/`. The documentation is therefore internally reasonable for the configured default, but "full suite" does not mean all repository pytest tests.

### 2. Primary CI

`.github/workflows/ci.yml` invokes:

```text
python scripts/run_ci_pytest_chunk.py --chunk-index N --chunk-total 4
```

The runner recursively enumerates only `tests/test_*.py` and `tests/**/test_*.py`. It does not enumerate `tests/integration/brain/`.

It also applies this keyword expression:

```text
-k "not platform_macos"
```

In the current working tree, this selects 1,900 of 1,919 default-suite tests and deselects 19. This is a name-based exclusion, not a marker-based platform policy. Any unrelated node containing `platform_macos` would also be excluded, while a macOS-only test with a different name relies on its own skip logic.

### 3. macOS runner

`scripts/run_macos_tests.command` explicitly runs:

```text
pytest tests tests/integration/brain -q
```

This runner has a broader pytest scope than the documented default and primary CI. The brain suite can therefore fail in the macOS gate after primary Windows/Linux CI has reported success.

### 4. Personal OS runner

`scripts/run_personal_os_tests.py` also includes `tests/integration/brain` in its pytest invocation and can add real-GUI or deeper macOS-native phases. It represents another broader definition of suite completeness.

### 5. App workflow runner

`scripts/run_app_workflow_tests.py` now combines:

- Twelve legacy workflow-oriented entry modules, including profile workflows and the inventory-manifest validator.
- Every test file referenced by the expanded 472-function workflow manifest (currently 69 manifest files and 73 files after merging the legacy list).
- One app-architecture module.
- One real-host module.

It adds log scanning (including worker `[crash] unhandled` diagnostics), isolated per-file execution on macOS, optional live-provider phases, and explicit temporary directories. Workflow-marked tests also receive a shared escaped Python/Qt exception collector. Real-worker workflows can opt into a shared process/thread/persistent-state inspector. These capabilities are valuable, but the fixed file list is another suite manifest that must be updated manually when workflow tests move or are added.

The expanded `tests/workflows/manifest.json` maps all 472 exact entries and all 3,296 failure references from `docs/APP_FUNCTION_INVENTORY.md` to real pytest node IDs. Completeness enforcement is enabled. Mapped nodes receive the workflow marker during collection, so they run through the shared runtime-failure collector even when their original module did not declare the marker.

### 6. Testlab

`testlab/` is a separate, purposeful system for real worker boot, GUI screenshots, hotkeys, speech paths, live model flow, and fresh dependency installation. It has smoke, release, and deep tiers and writes structured reports.

Testlab should remain separate from ordinary pytest because it has different cost, hardware, side-effect, timeout, and reporting requirements. Its separation is not itself test sprawl.

There is, however, a visibility tradeoff in its CI trigger: `.github/workflows/testlab.yml` runs on manual dispatch or pushes that touch `testlab/**` or the workflow itself. Changes to `core/`, `runtime/`, `ui/`, dependencies, or packaging do not automatically trigger Testlab even though those areas implement the behavior Testlab exercises. Comments in the workflow explain that this is an intentional cost-control decision, particularly for macOS runners.

### 7. Manual probes and orchestration

At least ten test-related programs exist outside automated pytest source directories, including:

- App workflow, personal OS, and macOS runners.
- macOS testbot and helper self-test programs.
- Microphone, released-speech, packaged-update, and Windows updater probes.

These are not necessarily redundant. The issue is discoverability: names such as `tools/test_mic.py` look like ordinary test modules, while orchestration entry points are distributed across `scripts/`, `tools/`, documentation, and workflow YAML.

## Configuration analysis

### Duplicate pytest configuration

The repository currently defines pytest discovery in two places:

`pytest.ini`:

```ini
[pytest]
pythonpath = .
testpaths = tests
markers = ...
```

`pyproject.toml`:

```toml
[tool.pytest.ini_options]
pythonpath = ["."]
testpaths = ["tests"]
```

Running pytest reports:

```text
configfile: pytest.ini (WARNING: ignoring pytest config in pyproject.toml!)
```

At present, the duplicated keys agree, so there is no demonstrated discovery bug from the duplication. The risk is future drift: a developer can edit `pyproject.toml`, see valid-looking pytest configuration, and receive no behavioral change because `pytest.ini` wins. The marker definitions exist only in `pytest.ini`, making the two files already asymmetric.

The lowest-risk immediate fix is to retain `pytest.ini` as canonical and remove `[tool.pytest.ini_options]` from `pyproject.toml`. Consolidating everything into `pyproject.toml` is also viable, but would require deleting `pytest.ini` and updating CI path filters and cache dependency lists that explicitly reference it.

### Fixture boundaries

There are three conftest files:

| File | Lines | Fixture declarations | Autouse fixture mentions |
|---|---:|---:|---:|
| `tests/conftest.py` | 144 | 2 | 1 |
| `tests/runtime/conftest.py` | 57 | 1 | 1 |
| `tests/integration/brain/conftest.py` | 67 | 2 | 1 |

This is a reasonable use of directory-scoped fixtures. If the brain tests move under `tests/`, their conftest should move with them so their setup boundary remains explicit.

### Framework style

Pytest is the universal runner, but the source style is mixed:

- 42 test modules import `unittest`.
- 38 test modules contain `unittest.TestCase` subclasses.
- Other modules use pytest functions, fixtures, parametrization, or mixtures of both.

Pytest supports this combination, so it is not a correctness defect. It does, however, make fixture usage, parametrization, assertion style, and test organization less uniform. Style conversion should be opportunistic when a file is already being substantially edited, not a repository-wide rewrite.

## CI distribution analysis

The CI chunk runner sorts test files and assigns every fourth file to the same chunk. This produces nearly equal file counts:

| Chunk | Files | Selected test cases | Largest contributors |
|---|---:|---:|---|
| 1 | 34 | 405 | Intent overlay, LLM fallbacks, chat render limits |
| 2 | 34 | 726 | Runtime flows, settings dialog, optional dependencies |
| 3 | 33 | 487 | Agent runner, developer environment, config environment |
| 4 | 33 | 282 | UI host reply, bubble transcript, native context |

Chunk 2 contains about **2.6 times** as many selected cases as chunk 4. Test count is still only a proxy for duration—a short parametrized unit test and a GUI/process test have different costs—but the current distribution is sufficiently uneven to show that file-count round-robin assignment is not a reliable balancing strategy.

The largest current contributors also happen to be concentrated in chunk 2:

- `tests/runtime/test_flows.py`: 161 collected test definitions/cases before parametrization effects.
- `tests/test_settings_dialog_controls.py`: 148 selected nodes in the CI collection.
- `tests/test_optional_deps.py`: 84 selected nodes.

Recommended progression:

1. Immediately balance chunks using collected node counts per file rather than file counts.
2. Capture actual per-file durations from CI artifacts.
3. Generate or update a deterministic weighted manifest using recent durations, with node count as the fallback for new files.

The runner should keep deterministic assignment so failures remain reproducible.

## Structural hotspots

### Largest test modules

| Test module | Physical lines | Test definitions/classes observed |
|---|---:|---:|
| `tests/runtime/test_flows.py` | 5,682 | 161 |
| `tests/test_settings_dialog_controls.py` | 5,486 | 141 |
| `tests/test_optional_deps.py` | 2,289 | 80 |
| `tests/test_agent_runner.py` | 2,244 | 94 |
| `tests/test_app_user_workflows.py` | 2,163 | 29 |
| `tests/test_intent_overlay.py` | 1,677 | 44 |
| `tests/test_llm_fallbacks.py` | 1,485 | 43 |
| `tests/runtime/test_ui_host_reply.py` | 1,426 | 47 |
| `tests/test_bubble_transcript.py` | 1,387 | 40 |
| `tests/test_config_env.py` | 1,291 | 46 |

Large files are not inherently poor tests, and line count should not become a hard quality gate. In this repository, however, the largest files create practical costs:

- They dominate CI chunks.
- They increase merge-conflict probability.
- They make ownership and failure localization harder.
- They encourage broad module setup and repeated skip decorators.
- They make fixed runner allowlists coarser than the behaviors being selected.

Splits should follow behavioral boundaries, not arbitrary line counts. For example, settings tests could be separated by provider settings, speech installation, appearance/localization, profiles, validation, and dialog lifecycle. Runtime flows could be separated by intent, chat, speech, agent, cancellation, and shutdown flows while keeping shared fixtures in a local conftest or helper module.

### Platform and dependency skip policy

The inspected source contains:

- 236 `@pytest.mark.skipif` occurrences.
- 209 `pytest.importorskip` occurrences.

Many UI files repeat PySide6 availability checks test by test. This is noisy and can make module-level collection behavior difficult to infer, especially when `pytest.importorskip(...)` is evaluated while decorators are being constructed.

Common capability gates should be centralized where semantics are truly shared:

- Module-level `pytestmark` for modules that are entirely UI-dependent or platform-specific.
- Named fixtures for Qt application lifecycle and optional runtime dependencies.
- Registered markers for platform or capability lanes where selection, rather than only skipping, is needed.

This should be done carefully: a pure helper test in a mostly UI-oriented module should not become skipped merely because the module received a broad UI gate. Splitting mixed modules first may be safer than immediately promoting every repeated decorator to module scope.

## Generated artifact analysis

Generated data contributes strongly to the visual impression that tests are everywhere:

| Ignored area | Files observed | Approximate size |
|---|---:|---:|
| `.tmp_pytest/` | 3,815 | 2.8 MB |
| `.pytest_cache/` | 5 | 0.2 MB |
| `build_logs/` | 104 | 45.3 MB |
| `testlab/.artifacts/` | 80 | 0.6 MB |
| `testlab/reports/` | 65 | 0.1 MB |

These paths are ignored and are not evidence that generated outputs are being committed. The main impact is local navigational noise and accumulated disk use. A documented cleanup command would help without changing test architecture.

Any cleanup implementation must use narrowly scoped, explicit directories and should avoid deleting logs or Testlab evidence unexpectedly. A safe design would support a dry run and separate switches for pytest temporary data, caches, workflow logs, and Testlab reports.

## Findings by priority

### High priority: no authoritative full-suite contract

**Evidence:** Default pytest and primary CI use `tests/`; macOS and personal OS runners add `tests/integration/brain/`.

**Impact:** A change can pass the documented local command and primary CI without the brain handler suite being collected. Different platforms make different claims when they say tests passed.

**Recommendation:** Make all ordinary pytest tests discoverable through one canonical root. The cleanest end state is to move `tests/integration/brain/` under a suitable `tests/` subtree. A lower-change interim step is `testpaths = tests tests/integration/brain`, coupled with CI enumeration of both roots.

### High priority: CI chunks are materially imbalanced

**Evidence:** Current selected node counts range from 282 to 726 despite near-equal file counts.

**Impact:** Longer feedback cycles, inefficient parallel capacity, and one chunk becoming the practical bottleneck.

**Recommendation:** Balance deterministically by collected nodes immediately and by observed duration after timing data is available.

### Medium priority: duplicate pytest configuration

**Evidence:** Pytest explicitly ignores the valid-looking pyproject pytest table because `pytest.ini` exists.

**Impact:** Configuration edits can silently have no effect and marker configuration is split conceptually across two candidate homes.

**Recommendation:** Keep one configuration source. Retaining `pytest.ini` is the smallest safe change.

### Medium priority: weak taxonomy and duplicated suite manifests

**Evidence:** Execution level is encoded through a mixture of directories, filenames, four exceptional markers, skip expressions, and fixed runner lists.

**Impact:** New tests can land in the wrong lane, expensive tests can leak into default execution, and moves require changes in several scripts.

**Recommendation:** Define canonical lanes and make runners select lanes through directories or markers rather than maintaining overlapping file lists.

### Medium priority: oversized modules

**Evidence:** Two files exceed 5,000 lines and five exceed 2,000 lines.

**Impact:** Slow review, high conflict surface, coarse ownership, and CI imbalance.

**Recommendation:** Split files opportunistically along behavior boundaries, beginning with the three modules dominating CI chunk 2.

### Low priority: manual-tool naming and generated clutter

**Evidence:** Manual utilities use `test_*` names, and ignored test output directories contain thousands of files.

**Impact:** Search noise and confusion for contributors, but little direct correctness risk because `testpaths` prevents accidental collection.

**Recommendation:** Rename manual probes to `diagnose_*`, `verify_*`, or `probe_*` when compatibility permits; add an explicit cleanup utility or documented commands.

### Low priority: mixed unittest and pytest styles

**Evidence:** 38 modules contain `unittest.TestCase` classes.

**Impact:** Inconsistent idioms, but pytest collects the tests successfully.

**Recommendation:** Do not perform a mass conversion. Normalize only while splitting or substantially revising a module.

## Recommended target model

Testlab should remain independent, while all ordinary pytest tests should live under one discoverable hierarchy. One workable direction is:

```text
tests/
  unit/
    core/
    runtime/
    ui/
  integration/
    brain/
    supervisor/
    workers/
    addons/
  workflows/
  platform/
    windows/
    macos/
    linux/
  real/
    host/
    providers/
  support/
testlab/
  checks/
```

This is a target model, not a recommendation for a single bulk move. Existing imports, conftest scope, MyPy package-base behavior, script allowlists, visual baseline paths, and platform permissions all make a big-bang reorganization unnecessarily risky.

A less disruptive component-first hierarchy is also defensible. The essential properties are:

- One default discovery root.
- Explicit boundaries for fast deterministic tests versus real or costly tests.
- One canonical selection mechanism per lane.
- Stable fixture scope.
- No manually duplicated list of files where a marker or directory can express the same intent.

## Phased remediation plan

### Phase 0: record the contract

1. Decide what "default," "CI," "workflow," "platform," "real," and "release" mean.
2. Document whether default tests may open windows, access devices, use the network, spend tokens, or mutate user data.
3. Give every documented command a precise scope rather than calling multiple different selections "full."

**Deliverable:** A short test matrix in the developer documentation.

### Phase 1: remove discovery ambiguity

1. Select one pytest configuration file.
2. Add the brain tests to the canonical default/CI contract, initially without moving files if necessary.
3. Update `run_ci_pytest_chunk.py` to enumerate the same roots as the canonical contract.
4. Update developer documentation and platform scripts to invoke the same base selection.
5. Add a collection contract test that compares expected roots or verifies representative test modules are included.

**Success criterion:** Default local collection, CI collection, and the base macOS collection report the same ordinary pytest set before platform deselection.

### Phase 2: fix CI balancing

1. Calculate per-file collected-node weights.
2. Assign files to the currently lightest chunk, using a deterministic ordering and tie-breaker.
3. Publish file assignment and totals in CI logs.
4. Later incorporate measured duration data.

**Success criterion:** No chunk has more than roughly 25% more estimated work than another, followed by a duration-based objective once timing is collected.

### Phase 3: consolidate execution lanes

1. Add only the markers that correspond to real selection requirements.
2. Replace name-based `-k` platform exclusions with explicit paths or markers.
3. Reduce fixed workflow file allowlists where markers can express the contract.
4. Keep live-provider, real-host, and Testlab checks opt-in or separately gated.

**Success criterion:** A contributor can determine how a test runs by its directory and markers without reading multiple runner scripts.

### Phase 4: split hotspots

Recommended starting order:

1. `tests/runtime/test_flows.py`
2. `tests/test_settings_dialog_controls.py`
3. `tests/test_optional_deps.py`
4. `tests/test_agent_runner.py`
5. `tests/test_app_user_workflows.py`

For each split:

- Preserve node behavior and fixtures first.
- Move one behavioral group at a time.
- Run old and new focused collections to ensure no tests disappear.
- Update runner references in the same change.
- Avoid simultaneous style rewrites unless required.

**Success criterion:** The largest files no longer dominate ownership or a single CI chunk, with no reduction in collected coverage.

### Phase 5: improve developer ergonomics

1. Add one discoverable test entry script or documented command matrix.
2. Rename manual diagnostic programs away from pytest-shaped names.
3. Add safe, scoped cleanup commands for generated artifacts.
4. Publish collection counts and slowest tests as CI artifacts to catch future drift.

## Proposed command contract

The exact interface can vary, but the repository should expose concepts equivalent to:

| Command | Contract |
|---|---|
| `test default` | Deterministic, no network spending, no real desktop/device mutation |
| `test ci` | Same logical suite as default, platform exclusions explicit, chunkable |
| `test workflow` | User-visible simulated workflows and UI contracts |
| `test platform` | Current-OS native integration checks |
| `test real` | Opt-in real host/provider/harness checks |
| `test release` | Testlab release gate with explicit hardware/cost expectations |

This can be implemented through direct pytest commands, a small Python wrapper, or existing scripts. The important part is a single source of truth for what each lane includes.

## What should not be changed merely for neatness

- Do not merge Testlab into ordinary pytest solely to reduce directory count.
- Do not move every test in one commit.
- Do not mass-convert `unittest.TestCase` modules that are stable.
- Do not apply module-wide dependency skips until mixed pure/UI modules are understood.
- Do not delete current logs or reports as part of organizational refactoring.
- Do not infer suite health from collection success; this audit did not run all 2,060 ordinary pytest tests.

## Verification checklist for future changes

- [ ] Pytest reports exactly one active configuration file with no ignored-config warning.
- [ ] The documented default command includes representative brain tests.
- [ ] Primary CI and local default enumerate the same ordinary pytest roots.
- [ ] Platform-only exclusions use an explicit marker or directory contract.
- [ ] Workflow runners no longer depend on stale file lists, or those lists are contract-tested.
- [ ] Collection totals before and after file moves account for every test.
- [ ] CI chunk assignment reports balanced estimated work.
- [ ] Real-host, live-provider, and Testlab lanes remain opt-in and clearly labeled.
- [ ] Generated artifact cleanup is scoped and recoverability expectations are documented.

## Conclusion

The repository has a substantial and valuable test estate rather than an absence of organization. Its weakness is that organization evolved in several parallel dimensions—component location, execution cost, platform, user workflow, and release realism—without one governing suite contract.

The immediate payoff will come from three small, high-leverage changes: remove the duplicate pytest configuration, bring brain tests into the default CI boundary, and balance CI by test weight instead of file count. Directory restructuring and large-file splits should follow gradually after those contracts are stable.
