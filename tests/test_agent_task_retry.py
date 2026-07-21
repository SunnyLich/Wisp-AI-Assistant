"""Tests for test agent task retry."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from core.agent.runner import AgentTaskRunner
from core.agent.task_spec import (
    canonical_agent_name,
    canonical_agent_role,
    canonical_communication_phase,
    continue_spec_from_run,
    default_agent_specs,
    default_communication_specs,
    localize_agent_spec_if_default,
    localize_communication_spec_if_default,
    retry_spec_from_run,
    role_responsibility,
)


class AgentTaskRetryTests(unittest.TestCase):
    def test_retry_spec_rejects_unavailable_task_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "missing-run-artifacts"
            run_dir.mkdir()

            with self.assertRaisesRegex(ValueError, "does not have task.json"):
                retry_spec_from_run(run_dir)

    def test_researcher_role_has_default_responsibility(self):
        self.assertIn("read-only context", role_responsibility("Researcher"))

    def test_agent_defaults_follow_assistant_language(self):
        """Verify built-in agent presets can be localized for model-facing specs."""
        agents = default_agent_specs("Chinese (Traditional)")
        communications = default_communication_specs("Chinese (Traditional)")

        self.assertEqual(agents[0]["name"], "協調員")
        self.assertEqual(agents[1]["name"], "建構者")
        self.assertEqual(agents[1]["role"], "實作者")
        self.assertIn("程式碼", agents[1]["responsibility"])
        self.assertEqual(communications[0]["from_agent"], "協調員")
        self.assertEqual(communications[0]["to_agent"], "建構者")
        self.assertEqual(communications[0]["phase"], "規劃")
        self.assertEqual(canonical_agent_name("建構者"), "Builder")
        self.assertEqual(canonical_agent_role("實作者"), "Implementer")
        self.assertEqual(canonical_communication_phase("規劃"), "Planning")

    def test_agent_default_localization_preserves_custom_edits(self):
        """Verify only built-in/default agent fields are localized."""
        english = default_agent_specs("English")[1]
        localized = localize_agent_spec_if_default(1, english, "Spanish")
        custom = localize_agent_spec_if_default(
            1,
            {**english, "name": "Fixer", "responsibility": "Only touch tests."},
            "Spanish",
        )

        self.assertEqual(localized["name"], "Constructor")
        self.assertEqual(localized["role"], "Implementador")
        self.assertIn("código", localized["responsibility"])
        self.assertEqual(custom["name"], "Fixer")
        self.assertEqual(custom["responsibility"], "Only touch tests.")
        self.assertEqual(custom["role"], "Implementador")

    def test_default_communication_routes_follow_custom_agent_names(self):
        """Verify localized default communication routes use the current agent names."""
        english_comm = default_communication_specs("English")[0]
        localized = localize_communication_spec_if_default(
            0,
            english_comm,
            "Chinese (Traditional)",
            ["Lead", "Fixer", "Reviewer"],
        )

        self.assertEqual(localized["from_agent"], "Lead")
        self.assertEqual(localized["to_agent"], "Fixer")
        self.assertEqual(localized["phase"], "規劃")
        self.assertIn("實作計畫", localized["message"])

    def test_localized_roles_keep_runner_permissions(self):
        """Verify localized built-in roles still map to runner capabilities."""
        spec = SimpleNamespace(
            allow_git=True,
            git_permission_mode="auto",
            allow_file_edit=True,
            file_edit_permission_mode="auto",
            allow_file_create=True,
            file_create_permission_mode="auto",
            allow_file_delete=False,
            file_delete_permission_mode="never",
            allow_shell=True,
            shell_permission_mode="auto",
        )

        implementer_tools = AgentTaskRunner._allowed_tool_names(
            spec,
            active_agent={"name": "建構者", "role": "實作者"},
        )
        coordinator_tools = AgentTaskRunner._allowed_tool_names(
            spec,
            active_agent={"name": "協調員", "role": "協調員"},
        )

        self.assertIn("edit_file", implementer_tools)
        self.assertIn("run_command", implementer_tools)
        self.assertNotIn("edit_file", coordinator_tools)
        self.assertNotIn("run_command", coordinator_tools)

    def test_retry_spec_loads_original_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "task.json").write_text(
                json.dumps({
                    "title": "Fix tests",
                    "objective": "Make tests pass.",
                    "scope_folder": tmp,
                    "provider": "copilot",
                    "model": "gpt-5.3-codex",
                }),
                encoding="utf-8",
            )

            spec = retry_spec_from_run(run_dir)

            self.assertEqual(spec.title, "Fix tests")
            self.assertEqual(spec.objective, "Make tests pass.")

    def test_continue_spec_adds_previous_run_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "task.json").write_text(
                json.dumps({
                    "title": "Fix tests",
                    "objective": "Make tests pass.",
                    "scope_folder": tmp,
                    "provider": "copilot",
                    "model": "gpt-5.3-codex",
                    "required_context": "Existing context.",
                }),
                encoding="utf-8",
            )
            (run_dir / "final.md").write_text("Previous final.", encoding="utf-8")
            (run_dir / "run.log").write_text(
                "\n".join([
                    "[11:00:00] model streaming response: 7024 chars received after 48.4s",
                    "[11:00:01] model response still streaming after 50s (7279 chars received)",
                    "[11:00:02] agent turn 4/59: Builder",
                    "[11:00:03] Builder thought: I need to patch snake_game.py.",
                    "[11:00:04] Builder tool call: patch_file",
                ]),
                encoding="utf-8",
            )

            spec = continue_spec_from_run(run_dir)

            self.assertEqual(spec.title, "Continue: Fix tests")
            self.assertIn("Existing context.", spec.required_context)
            self.assertIn("Previous final.", spec.required_context)
            self.assertIn("Previous useful run events", spec.required_context)
            self.assertIn("Builder tool call: patch_file", spec.required_context)
            self.assertNotIn("model streaming response", spec.required_context)
            self.assertNotIn("response still streaming", spec.required_context)


if __name__ == "__main__":
    unittest.main()
