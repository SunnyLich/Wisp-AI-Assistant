"""Addon redesign smoke tests.

This script exercises the addon redesign without touching the real ``addons/``
folder. It creates temporary addons, installs them into temporary addon
directories, and runs the actual addon manager/distribution code.

By default the dependency test verifies approval/hash behavior only. Pass
``--install-deps`` to create the per-addon virtualenv and install
``requests>=2.31`` as a real end-to-end dependency runtime test. Expected addon
failure tracebacks are hidden unless ``--verbose`` is passed.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import os
import shutil
import sys
import tempfile
import textwrap
import traceback
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
BRAIN_PATH = REPO_ROOT / "runtime" / "brain"
if str(BRAIN_PATH) not in sys.path:
    sys.path.insert(0, str(BRAIN_PATH))


@dataclass
class Result:
    """Model result."""
    name: str
    status: str
    detail: str = ""


class Smoke:
    """Model smoke."""
    def __init__(self, *, install_deps: bool, keep_temp: bool, verbose: bool) -> None:
        """Initialize the smoke instance."""
        self.install_deps = install_deps
        self.keep_temp = keep_temp
        self.verbose = verbose
        self.temp_root = Path(tempfile.mkdtemp(prefix="wisp-addon-smoke-"))
        self.sources = self.temp_root / "sources"
        self.addons_dir = self.temp_root / "addons"
        self.envs_dir = self.temp_root / "addon_envs"
        self.store_path = self.temp_root / "addons.json"
        self.sources.mkdir(parents=True, exist_ok=True)
        self.addons_dir.mkdir(parents=True, exist_ok=True)
        self.results: list[Result] = []
        self._patch_runtime_paths()

    def _patch_runtime_paths(self) -> None:
        """Handle patch runtime paths for smoke."""
        from core import addon_runtime, addon_store
        import core.addon_manager as addon_manager
        import core.plugin_manager as plugin_manager
        import core.system.paths as paths

        addon_runtime.ADDON_ENVS_DIR = self.envs_dir
        addon_store._STORE_PATH = self.store_path
        addon_manager.addon_store._STORE_PATH = self.store_path
        addon_manager._manager = None
        plugin_manager._manager = None
        paths.ADDONS_DIR = self.addons_dir

    def case_addons_dir(self, name: str) -> Path:
        """Handle case addons dir for smoke."""
        addons_dir = self.temp_root / "cases" / name / "addons"
        addons_dir.mkdir(parents=True, exist_ok=True)
        return addons_dir

    def cleanup(self) -> None:
        """Handle cleanup for smoke."""
        if self.keep_temp:
            print(f"\nKept temp root: {self.temp_root}")
            return
        shutil.rmtree(self.temp_root, ignore_errors=True)

    def record(self, name: str, status: str, detail: str = "") -> None:
        """Record the smoke workflow."""
        self.results.append(Result(name, status, detail))

    def run_case(self, name: str, fn: Callable[[], None]) -> None:
        """Run case."""
        diagnostics = io.StringIO()
        try:
            if self.verbose:
                fn()
            else:
                with contextlib.redirect_stderr(diagnostics):
                    fn()
            self.record(name, "PASS")
        except SkipCase as exc:
            self.record(name, "SKIP", str(exc))
        except Exception as exc:
            self.record(name, "FAIL", f"{type(exc).__name__}: {exc}")
            traceback.print_exc()
            captured = diagnostics.getvalue().strip()
            if captured:
                print("\nCaptured diagnostics from failed case:")
                print(captured[-4000:])

    def report(self) -> int:
        """Handle report for smoke."""
        print("\nAddon Redesign Smoke Results")
        print("=" * 32)
        for result in self.results:
            suffix = f" - {result.detail}" if result.detail else ""
            print(f"{result.status:4}  {result.name}{suffix}")
        failures = [item for item in self.results if item.status == "FAIL"]
        skipped = [item for item in self.results if item.status == "SKIP"]
        print("=" * 32)
        print(f"{len(self.results) - len(failures) - len(skipped)} passed, {len(skipped)} skipped, {len(failures)} failed")
        return 1 if failures else 0


class SkipCase(RuntimeError):
    """Model skip case."""
    pass


def write(path: Path, text: str) -> None:
    """Write dedented text to *path*, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).strip() + "\n", encoding="utf-8")


def make_addon(root: Path, folder_name: str, manifest: str, code: str) -> Path:
    """Create addon."""
    folder = root / folder_name
    write(folder / "addon.toml", manifest)
    write(folder / "__init__.py", code)
    return folder


def manager_for(addons_dir: Path):
    """Support command-line helper for scripts addon redesign smoke for manager for."""
    from core.addon_manager import AddonManager

    manager = AddonManager(addons_dir)
    manager.load_all()
    return manager


def summary_by_id(manager, addon_id: str) -> dict:
    """Support command-line helper for scripts addon redesign smoke for summary by id."""
    for item in manager.summaries():
        if item.get("id") == addon_id:
            return item
    raise AssertionError(f"missing addon summary: {addon_id}")


def test_manifest_host_surfaces_and_distribution(smoke: Smoke) -> None:
    """Verify manifest host surfaces and distribution behavior."""
    from core.addon_distribution import install_addon_archive, install_addon_folder

    source = make_addon(
        smoke.sources,
        "core_contract",
        """
        [addon]
        id = "addon-smoke-core"
        name = "Addon Smoke Core"
        version = "1.2.3"
        description = "Exercises the core addon redesign contract."
        entry = "__init__.py"
        priority = 10

        [permissions]
        query = "modify"
        response = "read"
        tools = true
        hotkeys = true
        ui = ["tray", "settings", "intents", "notifications"]
        events = ["app.startup", "custom.event", "response.after"]

        [[intents]]
        id = "static-intent"
        label = "Static Intent"
        key = "s"
        prompt = "Static prompt"

        [[notifications]]
        title = "Static Notice"
        message = "Static ready"

        [[hotkeys]]
        id = "static-hotkey"
        label = "Static Hotkey"
        hotkey = "ctrl+alt+shift+s"
        prompt = "Static hotkey prompt"
        """,
        """
        import os
        import sys
        from core.plugin_manager import plugin_setting

        def before_query(prompt, context):
            print("addon-smoke-core before_query", file=sys.stderr, flush=True)
            return prompt + "|addon:" + str(os.getpid()), context + "|context"

        def after_response(text):
            print("addon-smoke-core after_response " + str(len(text)), file=sys.stderr, flush=True)

        def on_event(event, payload):
            return {"event": event, "payload_keys": sorted((payload or {}).keys())}

        def get_tray_actions():
            return [{"label": "Core Action", "callback": lambda: None}]

        def get_settings():
            return [{"key": "greeting", "label": "Greeting", "type": "text", "default": "hi"}]

        def get_intents():
            return [{"id": "dynamic-intent", "label": "Dynamic Intent", "prompt": "Dynamic prompt"}]

        def get_notifications():
            return [{"title": "Dynamic Notice", "message": "Dynamic ready"}]

        def get_hotkeys():
            return [{"id": "dynamic-hotkey", "label": "Dynamic Hotkey", "hotkey": "ctrl+alt+shift+d", "callback": lambda payload: {"message": "dynamic hotkey"}}]

        def get_tools():
            return [{
                "name": "addon_smoke_tool",
                "description": "Smoke tool",
                "input_schema": {"type": "object", "properties": {}, "required": []},
                "executor": lambda inputs: "tool ok",
            }]
        """,
    )

    folder_addons_dir = smoke.case_addons_dir("core-folder")
    archive_addons_dir = smoke.case_addons_dir("core-archive")
    folder_result = install_addon_folder(source, folder_addons_dir)
    assert folder_result["id"] == "addon-smoke-core", folder_result

    archive = smoke.temp_root / "core-contract.wisp"
    with zipfile.ZipFile(archive, "w") as zf:
        for path in source.rglob("*"):
            if path.is_file():
                zf.write(path, Path("core_contract") / path.relative_to(source))
    archive_result = install_addon_archive(archive, archive_addons_dir)
    assert archive_result["id"] == "addon-smoke-core", archive_result
    assert (archive_addons_dir / "addon-smoke-core" / "addon.toml").exists()

    manager = manager_for(folder_addons_dir)
    try:
        summary = summary_by_id(manager, "addon-smoke-core")
        assert summary["status"] == "loaded", summary
        assert summary["enabled"] is True, summary
        assert summary["permissions"]["query"] == "modify", summary
        assert {"before_query", "after_response", "on_event", "get_tools"}.issubset(set(summary["hooks"])), summary["hooks"]
        assert summary["tools"] == ["addon_smoke_tool"], summary["tools"]
        assert manager.get_tray_actions()[0]["label"] == "Core Action"
        assert manager.get_settings("addon-smoke-core")[0]["value"] == "hi"
        manager.set_setting("addon-smoke-core", "greeting", "hello")
        assert manager.get_settings("addon-smoke-core")[0]["value"] == "hello"

        prompt, context = manager.before_query("hello", "ctx")
        assert prompt.startswith("hello|addon:"), prompt
        assert context == "ctx|context", context
        host_pid = int(prompt.rsplit(":", 1)[1])
        assert host_pid != os.getpid(), "addon hook should run in a host process"

        events = manager.dispatch_event("custom.event", {"answer": 42})
        assert events == [{"addon_id": "addon-smoke-core", "event": "custom.event", "payload_keys": ["answer"]}], events
        manager.after_response("answer")

        intents = {item["id"] for item in manager.get_intents()}
        assert intents == {"static-intent", "dynamic-intent"}, intents
        notifications = {(item["title"], item["message"]) for item in manager.get_notifications()}
        assert notifications == {("Static Notice", "Static ready"), ("Dynamic Notice", "Dynamic ready")}, notifications
        hotkeys = {item["id"] for item in manager.get_hotkeys()}
        assert hotkeys == {"static-hotkey", "dynamic-hotkey"}, hotkeys
        assert manager.run_hotkey("addon-smoke-core", "static-hotkey") == {"prompt": "Static hotkey prompt"}
        assert manager.run_hotkey("addon-smoke-core", "dynamic-hotkey") == {"message": "dynamic hotkey"}

        logs = summary_by_id(manager, "addon-smoke-core")["logs"]
        assert "addon-smoke-core before_query" in logs, logs

        manager.set_enabled("addon-smoke-core", False)
        disabled = summary_by_id(manager, "addon-smoke-core")
        assert disabled["status"] == "disabled", disabled
        assert manager.get_intents() == []
        manager.set_enabled("addon-smoke-core", True)
        assert summary_by_id(manager, "addon-smoke-core")["status"] == "loaded"
    finally:
        manager.on_shutdown()


def test_dependency_runtime(smoke: Smoke) -> None:
    """Verify dependency runtime behavior."""
    from core import addon_runtime, addon_store
    from core.addon_distribution import install_addon_folder

    addons_dir = smoke.case_addons_dir("dependency")
    source = make_addon(
        smoke.sources,
        "deps_requests",
        """
        [addon]
        id = "addon-smoke-deps"
        name = "Addon Smoke Deps"
        entry = "__init__.py"

        [permissions]
        query = "modify"

        [dependencies]
        packages = ["requests>=2.31"]
        """,
        """
        import requests

        def before_query(prompt, context):
            return prompt + "\\n[requests=" + requests.__version__ + "]", context
        """,
    )
    install_addon_folder(source, addons_dir)
    manager = manager_for(addons_dir)
    try:
        summary = summary_by_id(manager, "addon-smoke-deps")
        assert summary["status"] == "needs_approval", summary
        assert summary["runtime"]["needs_approval"] is True, summary["runtime"]
        assert summary["runtime"]["packages"] == ["requests>=2.31"], summary["runtime"]
        assert manager.before_query("hello", "") == ("hello", "")

        if smoke.install_deps:
            repaired = manager.repair_environment("addon-smoke-deps")
            assert repaired.get("ready") is True, repaired
            prompt, _context = manager.before_query("hello", "")
            assert "[requests=" in prompt, prompt
        else:
            deps = addon_runtime.dependencies_from_manifest({"packages": ["requests>=2.31"]})
            addon_store.set_approved_dependency_hash("addon-smoke-deps", addon_runtime.dependency_hash(deps))

        manifest = addons_dir / "addon-smoke-deps" / "addon.toml"
        text = manifest.read_text(encoding="utf-8")
        manifest.write_text(text.replace('packages = ["requests>=2.31"]', 'packages = ["requests>=2.31", "packaging>=23"]'), encoding="utf-8")
        manager.load_all()
        changed = summary_by_id(manager, "addon-smoke-deps")
        assert changed["status"] == "needs_approval", changed
        assert changed["runtime"]["needs_approval"] is True, changed["runtime"]

        if smoke.install_deps:
            repaired = manager.repair_environment("addon-smoke-deps")
            assert repaired.get("ready") is True, repaired
    finally:
        manager.on_shutdown()


def test_bad_addon_resilience(smoke: Smoke) -> None:
    """Verify bad addon resilience behavior."""
    from core.addon_distribution import install_addon_folder

    addons_dir = smoke.case_addons_dir("bad-resilience")
    addons = [
        make_addon(
            smoke.sources,
            "bad_syntax",
            """
            [addon]
            id = "addon-smoke-bad-syntax"
            name = "Bad Syntax"
            entry = "__init__.py"

            [permissions]
            query = "modify"
            """,
            """
            def before_query(prompt, context):
                return prompt, context

            def intentionally_invalid(
            """,
        ),
        make_addon(
            smoke.sources,
            "bad_import",
            """
            [addon]
            id = "addon-smoke-bad-import"
            name = "Bad Import"
            entry = "__init__.py"

            [permissions]
            query = "modify"
            """,
            """
            import definitely_missing_addon_smoke_package

            def before_query(prompt, context):
                return prompt, context
            """,
        ),
        make_addon(
            smoke.sources,
            "hook_raises",
            """
            [addon]
            id = "addon-smoke-hook-raises"
            name = "Hook Raises"
            entry = "__init__.py"

            [permissions]
            query = "modify"
            response = "read"
            """,
            """
            def before_query(prompt, context):
                raise RuntimeError("intentional addon smoke hook failure")

            def after_response(text):
                raise RuntimeError("intentional addon smoke response failure")
            """,
        ),
        make_addon(
            smoke.sources,
            "malformed_surfaces",
            """
            [addon]
            id = "addon-smoke-malformed"
            name = "Malformed Surfaces"
            entry = "__init__.py"

            [permissions]
            ui = ["intents", "notifications"]
            hotkeys = true
            """,
            """
            def get_intents():
                return {"bad": "shape"}

            def get_notifications():
                return {"bad": "shape"}

            def get_hotkeys():
                return {"bad": "shape"}
            """,
        ),
        make_addon(
            smoke.sources,
            "host_exit",
            """
            [addon]
            id = "addon-smoke-host-exit"
            name = "Host Exit"
            entry = "__init__.py"

            [permissions]
            query = "modify"
            """,
            """
            import os

            def before_query(prompt, context):
                os._exit(42)
            """,
        ),
    ]
    for addon in addons:
        install_addon_folder(addon, addons_dir)

    manager = manager_for(addons_dir)
    try:
        summaries = manager.summaries()
        assert len(summaries) == 5, summaries
        assert "SyntaxError" in summary_by_id(manager, "addon-smoke-bad-syntax")["error"]
        assert "definitely_missing_addon_smoke_package" in summary_by_id(manager, "addon-smoke-bad-import")["error"]
        assert manager.get_intents() == [], manager.get_intents()
        assert manager.get_notifications() == [], manager.get_notifications()
        assert manager.get_hotkeys() == [], manager.get_hotkeys()

        prompt, context = manager.before_query("hello", "ctx")
        assert (prompt, context) == ("hello", "ctx")
        assert "intentional addon smoke hook failure" in summary_by_id(manager, "addon-smoke-hook-raises")["error"]
        assert "addon host exited" in summary_by_id(manager, "addon-smoke-host-exit")["error"]

        manager.after_response("answer")
    finally:
        manager.on_shutdown()


def test_permission_gating(smoke: Smoke) -> None:
    """Verify permission gating behavior."""
    from core.addon_distribution import install_addon_folder
    import core.plugin_manager as plugin_manager
    import core.system.paths as paths
    from wisp_brain import handlers

    addons_dir = smoke.case_addons_dir("permission-gating")
    source = make_addon(
        smoke.sources,
        "permissions_locked",
        """
        [addon]
        id = "addon-smoke-permissions-locked"
        name = "Permissions Locked"
        entry = "__init__.py"

        [permissions]
        query = "read"
        """,
        """
        def before_query(prompt, context):
            return prompt + " SHOULD_NOT_MODIFY", context

        def get_tray_actions():
            return [{"label": "SHOULD NOT APPEAR", "callback": lambda: None}]

        def get_settings():
            return [{"key": "blocked", "label": "SHOULD NOT APPEAR", "type": "text", "default": "x"}]

        def get_intents():
            return [{"id": "blocked", "label": "SHOULD NOT APPEAR", "prompt": "blocked"}]

        def get_notifications():
            return [{"title": "SHOULD NOT APPEAR", "message": "blocked"}]

        def get_hotkeys():
            return [{"id": "blocked", "label": "SHOULD NOT APPEAR", "hotkey": "ctrl+alt+shift+l"}]

        def get_tools():
            return [{
                "name": "phase4_blocked_tool",
                "description": "SHOULD NOT APPEAR",
                "input_schema": {"type": "object", "properties": {}, "required": []},
                "executor": lambda inputs: "blocked",
            }]
        """,
    )
    install_addon_folder(source, addons_dir)
    manager = manager_for(addons_dir)
    try:
        assert manager.get_tray_actions() == [], manager.get_tray_actions()
        assert manager.get_settings("addon-smoke-permissions-locked") == []
        assert manager.get_intents() == [], manager.get_intents()
        assert manager.get_notifications() == [], manager.get_notifications()
        assert manager.get_hotkeys() == [], manager.get_hotkeys()
        assert summary_by_id(manager, "addon-smoke-permissions-locked")["tools"] == []
        prompt, _context = manager.before_query("hello", "")
        assert "SHOULD_NOT_MODIFY" not in prompt, prompt

        paths.ADDONS_DIR = addons_dir
        plugin_manager._manager = manager
        try:
            handlers.HANDLERS["brain.plugins.llm_call"](
                plugin_name="addon-smoke-permissions-locked",
                prompt="hello",
            )
        except PermissionError as exc:
            assert "llm" in str(exc).lower(), exc
        else:
            raise AssertionError("LLM call unexpectedly succeeded without llm permission")
    finally:
        manager.on_shutdown()


def test_archive_path_traversal(smoke: Smoke) -> None:
    """Verify archive path traversal behavior."""
    from core.addon_distribution import install_addon_archive

    archive = smoke.temp_root / "bad-traversal.wisp"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../evil.txt", "nope")
        zf.writestr("safe/addon.toml", "[addon]\nid = 'addon-smoke-bad-archive'\nname = 'Bad Archive'\n")
        zf.writestr("safe/__init__.py", "")

    try:
        install_addon_archive(archive, smoke.addons_dir)
    except ValueError as exc:
        assert "unsafe" in str(exc).lower(), exc
    else:
        raise AssertionError("path traversal archive unexpectedly installed")
    assert not (smoke.temp_root / "evil.txt").exists()


def main() -> int:
    """Support command-line helper for scripts addon redesign smoke for main."""
    parser = argparse.ArgumentParser(description="Run addon redesign smoke tests.")
    parser.add_argument(
        "--install-deps",
        action="store_true",
        help="Actually create the per-addon env and install requests/packaging.",
    )
    parser.add_argument(
        "--keep-temp",
        action="store_true",
        help="Keep the temporary test directory for inspection.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show expected addon failure tracebacks and addon manager diagnostics.",
    )
    args = parser.parse_args()

    smoke = Smoke(install_deps=args.install_deps, keep_temp=args.keep_temp, verbose=args.verbose)
    try:
        smoke.run_case("Manifest, host, settings, surfaces, and distribution", lambda: test_manifest_host_surfaces_and_distribution(smoke))
        smoke.run_case("Dependency approval/hash behavior", lambda: test_dependency_runtime(smoke))
        if not args.install_deps:
            smoke.record(
                "Real dependency env install",
                "SKIP",
                "rerun with --install-deps to create env and pip-install requests",
            )
        smoke.run_case("Bad addon resilience", lambda: test_bad_addon_resilience(smoke))
        smoke.run_case("Permission gating", lambda: test_permission_gating(smoke))
        smoke.run_case("Archive path traversal rejection", lambda: test_archive_path_traversal(smoke))
        return smoke.report()
    finally:
        smoke.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
