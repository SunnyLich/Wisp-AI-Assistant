# Phase 4 Manual Addon Test Kit

These addons are for manual QA of the Phase 4 addon surfaces. They live outside
`addons/` on purpose. Install one at a time from the Addon Manager with
`Install folder`, run the checks, then disable or delete the installed copy from
`addons/`.

## Dependency Runtime

Install `deps_requests/`.

Expected:
- Addon Manager shows `phase4-deps-requests`.
- Status starts as needing dependency approval/install.
- Runtime packages include `requests>=2.31`.
- After approving/installing the environment, the addon loads.
- Sending a prompt runs `before_query` and appends the installed requests
  version to the prompt.

To test dependency changes:
1. Edit `addons/phase4-deps-requests/addon.toml`.
2. Change `packages = ["requests>=2.31"]` to
   `packages = ["requests>=2.31", "packaging>=23"]`.
3. Restart Wisp or reload addons.
4. Addon Manager should require approval again.
5. Approve/repair the environment.
6. The dependency environment should return to ready.

## Bad Addon Resilience

Install and test these one at a time:

- `bad_syntax/`: import should fail with a syntax error, while Wisp stays open.
- `bad_import/`: import should fail with a missing module error, while Wisp
  stays open.
- `hook_raises/`: addon loads, but a normal prompt triggers a hook exception.
  Wisp should still answer or recover normally.
- `malformed_surfaces/`: addon returns malformed intents, notifications, and
  hotkeys. Wisp should filter/ignore bad entries and stay open.
- `host_exit/`: a normal prompt force-exits the addon host process. Wisp should
  stay open and show/log the addon failure.

For path traversal archive testing:

```powershell
python manual_test_addons/phase4/make_bad_archive.py
```

Then open Addon Manager, choose `Install archive`, and select
`manual_test_addons/phase4/bad-traversal.wisp`.

Expected:
- Install fails with an unsafe archive/path traversal error.
- No `evil.txt` file appears outside the install target.
- Wisp stays open.

## Permission Gating

Install `permissions_locked/`.

Expected:
- No tray actions from this addon.
- No settings button content from this addon.
- No intent picker entries from this addon.
- No addon notifications from this addon.
- No addon hotkeys from this addon.
- No model tool from this addon.
- A direct addon LLM call is rejected because `llm = true` is not declared.

To test the LLM rejection from PowerShell while Wisp is using this repo:

```powershell
$env:PYTHONPATH = "runtime/brain"
.\.venv\Scripts\python.exe -c "from wisp_brain import handlers; handlers.HANDLERS['brain.plugins.llm_call'](plugin_name='phase4-permissions-locked', prompt='hello')"
```

Expected:
- The command raises `PermissionError` mentioning missing `llm` permission.

