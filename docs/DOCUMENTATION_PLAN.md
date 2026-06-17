# Full Documentation Sweep For Wisp

## Summary

- Add source-level documentation across the full repo: module/file summaries and concise docstrings for every parseable Python function, method, class, and nested helper.
- Create `docs/DEVELOPER_README.md` for developer onboarding and `docs/COMMUNICATION_GRAPH.md` with Mermaid diagrams showing how Wisp's files/subsystems interact.
- Link both new docs from the root `README.md` without turning the user-facing README into a developer manual.

## Key Changes

- Python files: add or refresh top module docstrings and callable/class docstrings using concise triple-quoted docstrings, preserving behavior, decorators, imports, BOMs, shebangs, and existing comments.
- Non-Python source/config files: add header comments only where the format supports comments. JSON, binaries, logs, generated translations, archives, lock files, runtime data, and media assets will not be modified.
- Negative-test fixtures: preserve intentionally broken files such as `manual_test_addons/phase4/bad_syntax/__init__.py`; add only safe explanatory headers/comments where possible, and document skipped callable coverage as intentional.
- Communication graph: document supervisor-first flow, worker IPC, UI/native/audio/brain boundaries, core services, addons, config/secrets, and memory/chat persistence.
- Developer README: include repo map, setup, launch commands, test/lint/type-check commands, architecture overview, addon notes, debugging/log locations, and documentation conventions.

## Verification

- Run an AST documentation audit over all parseable Python files to confirm zero missing module/function/class docstrings in scope.
- Run syntax/compile verification for all parseable Python files while excluding intentionally invalid fixtures.
- Run the project's existing pytest suite if feasible: `.\.venv\Scripts\python.exe -m pytest`.
- Run the existing focused Ruff/MyPy commands from the README to confirm the documentation sweep did not introduce style or type regressions.

## Assumptions

- Scope is full repo source, including tests, experiments, scripts, tools, addons, and manual test addons.
- "Top of each function" means Python docstrings as the first statement inside each callable.
- Documentation should be explanatory but compact, avoiding noisy parameter-by-parameter blocks unless needed for clarity.
- No runtime APIs, schemas, environment variables, or user-facing behavior should change.
