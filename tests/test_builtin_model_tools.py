"""Tests for test builtin model tools."""

import unittest
from unittest.mock import patch
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import config
from core.llm_clients import client as llm
from core.llm_clients import prompt_guidance


class BuiltinModelToolsTests(unittest.TestCase):
    """Test case for builtin model tools tests behavior."""
    _GIT_TOOLS = {"git_status", "git_diff", "github_repo", "github_issue"}

    def test_git_and_github_tools_are_registered(self):
        """Verify git and github tools are registered behavior."""
        names = {schema["name"] for schema in llm._TOOL_REGISTRY.schemas()}

        self.assertTrue(self._GIT_TOOLS <= names)

    def test_git_and_github_tools_surface_for_relevant_prompt(self):
        # These tools are keyword-gated (see tool_keywords.json): an empty prompt
        # excludes them, but a relevant prompt brings them back.
        """Verify git and github tools surface for relevant prompt behavior."""
        empty = {schema["name"] for schema in llm._get_tool_schemas("")}
        self.assertTrue(self._GIT_TOOLS.isdisjoint(empty))

        relevant = {
            schema["name"]
            for schema in llm._get_tool_schemas(
                "show me the git status and git diff, and the github repo and issue"
            )
        }
        self.assertTrue(self._GIT_TOOLS <= relevant)

    def test_allowed_tool_filter_limits_general_schemas(self):
        """Verify allowed tool filter limits general schemas behavior."""
        prompt = "show me the git status and github issue, then search the web"

        names = {
            schema["name"]
            for schema in llm._get_tool_schemas(
                prompt,
                allowed_tools=["web_search", "get_context.browser"],
            )
        }

        self.assertIn("web_search", names)
        self.assertIn("get_context", names)
        self.assertTrue(self._GIT_TOOLS.isdisjoint(names))

    def test_memory_search_is_opt_in(self):
        """Verify memory search is opt in behavior."""
        default_names = {schema["name"] for schema in llm._get_tool_schemas("remember my project")}
        allowed_names = {
            schema["name"]
            for schema in llm._get_tool_schemas(
                "remember my project",
                allowed_tools=["memory_search"],
            )
        }

        self.assertNotIn("memory_search", default_names)
        self.assertIn("memory_search", allowed_names)

    def test_memory_search_note_only_when_tool_offered(self):
        """Verify memory search note only when tool offered behavior."""
        base = "You are a concise desktop assistant."

        self.assertIn("memory_search tool", llm._with_memory_search_note(base, ["memory_search"]))
        self.assertEqual(llm._with_memory_search_note(base, ["memory_save"]), base)
        self.assertEqual(llm._with_memory_search_note(base, None), base)

    def test_prompt_guidance_builds_query_notes_from_one_place(self):
        """Verify prompt guidance builds query notes from one place behavior."""
        system = prompt_guidance.apply_query_guidance(
            "Base prompt",
            tools_offered=True,
            allowed_tools=["memory_search", "memory_save"],
            allow_screenshot_tool=True,
        )

        self.assertIn("live tools available", system)
        self.assertIn("capture_screen tool", system)
        self.assertIn("memory_search tool", system)
        self.assertIn("memory_save tool", system)

    def test_frontloaded_memory_search_uses_query(self):
        """Verify frontloaded memory search uses query behavior."""
        captured = {}

        class FakeManager:
            """Coordinate fake manager behavior."""
            def retrieve_relevant(self, query):
                """Verify retrieve relevant behavior."""
                captured["query"] = query
                return "[Memory]\n- I prefer concise answers"

        with patch("core.memory_store.store.get_manager", return_value=FakeManager()):
            ambient = llm._inject_frontloaded_tool_context(
                "Active app: Notes",
                ["memory_search"],
                query="what do you remember about my answer style?",
            )

        self.assertIn("Active app: Notes", ambient)
        self.assertIn("[Memory]", ambient)
        self.assertEqual(captured["query"], "what do you remember about my answer style?")

    def test_frontloaded_memory_search_stays_opt_in(self):
        """Verify frontloaded memory search stays opt in behavior."""
        with patch("core.memory_store.store.get_manager") as get_manager:
            ambient = llm._inject_frontloaded_tool_context(
                "Active app: Notes",
                [],
                query="what do you remember?",
            )

        self.assertEqual(ambient, "Active app: Notes")
        get_manager.assert_not_called()

    def test_get_context_execution_respects_source_allowlist(self):
        """Verify get context execution respects source allowlist behavior."""
        self.assertIn(
            "disabled",
            llm._execute_model_tool(
                "get_context",
                {},
                allowed_tools=["get_context.browser"],
            ),
        )
        self.assertIn(
            "disabled",
            llm._execute_model_tool(
                "get_context",
                {"url": "https://example.com"},
                allowed_tools=["get_context.documents"],
            ),
        )

    def test_pinned_tools_bypass_keyword_filter(self):
        # git_status is keyword-gated, so an unrelated prompt drops it even when
        # allowed — unless it is pinned ("On" in the per-caller tool list).
        """Verify pinned tools bypass keyword filter behavior."""
        filtered = {
            schema["name"]
            for schema in llm._get_tool_schemas("hello", allowed_tools=["git_status"])
        }
        pinned = {
            schema["name"]
            for schema in llm._get_tool_schemas(
                "hello",
                allowed_tools=["git_status"],
                pinned_tools=["git_status"],
            )
        }

        self.assertNotIn("git_status", filtered)
        self.assertIn("git_status", pinned)

    def test_pinned_tools_bypass_keyword_filter_openai_format(self):
        """Verify pinned tools bypass keyword filter openai format behavior."""
        pinned = {
            (schema.get("function") or {}).get("name")
            for schema in llm._get_openai_tool_schemas(
                "hello",
                allowed_tools=["git_status"],
                pinned_tools=["git_status"],
            )
        }

        self.assertIn("git_status", pinned)

    def test_pinned_context_source_grants_offer_get_context_schema(self):
        """Verify pinned context source grants offer get context schema behavior."""
        names = {
            schema["name"]
            for schema in llm._get_tool_schemas(
                "hello",
                allowed_tools=["get_context.browser"],
                pinned_tools=["get_context"],
            )
        }

        self.assertIn("get_context", names)

    def test_pinned_browser_mode_offers_web_and_context_for_anthropic(self):
        """Verify pinned browser mode offers web and context for anthropic behavior."""
        names = {
            schema["name"]
            for schema in llm._get_tool_schemas(
                "hello",
                allowed_tools=["web_search", "get_context.browser"],
                pinned_tools=["web_search", "get_context"],
            )
        }

        self.assertIn("web_search", names)
        self.assertIn("get_context", names)

    def test_pinned_browser_mode_openai_offers_context_function(self):
        """Verify pinned browser mode openai offers context function behavior."""
        names = {
            (schema.get("function") or {}).get("name")
            for schema in llm._get_openai_tool_schemas(
                "hello",
                allowed_tools=["web_search", "get_context.browser"],
                pinned_tools=["web_search", "get_context"],
            )
        }

        self.assertIn("get_context", names)
        self.assertNotIn("web_search", names)

    def test_pinned_opt_in_tools_are_not_added(self):
        # capture_screen is governed by the screenshot setting, never by the
        # per-caller tool list, even if someone hand-writes it into the env.
        """Verify pinned opt in tools are not added behavior."""
        names = {
            schema["name"]
            for schema in llm._get_tool_schemas(
                "hello",
                allowed_tools=["capture_screen"],
                pinned_tools=["capture_screen"],
            )
        }

        self.assertNotIn("capture_screen", names)

    def test_local_file_tools_are_explicit_opt_in(self):
        """Verify local file tools only surface when a caller grants them."""
        default_names = {schema["name"] for schema in llm._get_tool_schemas("edit this file")}
        model_names = {
            schema["name"]
            for schema in llm._get_tool_schemas(
                "edit this file",
                allowed_tools=["edit_file"],
            )
        }
        irrelevant_names = {
            schema["name"]
            for schema in llm._get_tool_schemas(
                "hello",
                allowed_tools=["edit_file"],
            )
        }
        pinned_names = {
            schema["name"]
            for schema in llm._get_tool_schemas(
                "hello",
                allowed_tools=["edit_file"],
                pinned_tools=["edit_file"],
            )
        }

        self.assertNotIn("edit_file", default_names)
        self.assertIn("edit_file", model_names)
        self.assertNotIn("edit_file", irrelevant_names)
        self.assertIn("edit_file", pinned_names)

    def test_local_file_tools_execute_with_scope_and_approval(self):
        """Verify scoped read/edit behavior for live local file tools."""
        old_roots = getattr(config, "TOOL_FILE_ROOTS", [])
        old_mode = getattr(config, "TOOL_FILE_MODE", "never")
        old_blocked = getattr(config, "TOOL_FILE_BLOCKED_GLOBS", [])
        llm.set_file_edit_approval_callback(None)
        try:
            with TemporaryDirectory() as tmp:
                root = Path(tmp)
                note = root / "note.txt"
                note.write_text("hello world", encoding="utf-8")
                secret = root / ".env"
                secret.write_text("TOKEN=shh", encoding="utf-8")

                config.TOOL_FILE_ROOTS = [str(root)]
                config.TOOL_FILE_BLOCKED_GLOBS = [".env*", "**/.env*"]
                config.TOOL_FILE_MODE = "never"

                self.assertIn(
                    "hello world",
                    llm._execute_model_tool(
                        "read_file",
                        {"path": str(note)},
                        allowed_tools=["read_file"],
                    ),
                )
                self.assertIn(
                    "failed",
                    llm._execute_model_tool(
                        "read_file",
                        {"path": str(secret)},
                        allowed_tools=["read_file"],
                    ),
                )
                self.assertIn(
                    "disabled",
                    llm._execute_model_tool(
                        "edit_file",
                        {"path": str(note), "old": "world", "new": "there"},
                        allowed_tools=["edit_file"],
                    ),
                )
                self.assertEqual(note.read_text(encoding="utf-8"), "hello world")

                approvals: list[dict] = []
                config.TOOL_FILE_MODE = "ask"
                llm.set_file_edit_approval_callback(lambda request: approvals.append(request) or True)
                result = llm._execute_model_tool(
                    "edit_file",
                    {"path": str(note), "old": "world", "new": "there"},
                    allowed_tools=["edit_file"],
                )

                self.assertIn("Edited", result)
                self.assertEqual(note.read_text(encoding="utf-8"), "hello there")
                self.assertEqual(approvals[0]["action"], "edit_file")
                self.assertIn("-hello world", approvals[0]["diff"])
        finally:
            config.TOOL_FILE_ROOTS = old_roots
            config.TOOL_FILE_MODE = old_mode
            config.TOOL_FILE_BLOCKED_GLOBS = old_blocked
            llm.set_file_edit_approval_callback(None)

    def test_local_file_edit_refuses_stale_approval_preview(self):
        """Verify ask-mode edits fail if the file changes after preview approval."""
        old_roots = getattr(config, "TOOL_FILE_ROOTS", [])
        old_mode = getattr(config, "TOOL_FILE_MODE", "never")
        old_blocked = getattr(config, "TOOL_FILE_BLOCKED_GLOBS", [])
        try:
            with TemporaryDirectory() as tmp:
                root = Path(tmp)
                note = root / "note.txt"
                note.write_text("alpha beta", encoding="utf-8")
                config.TOOL_FILE_ROOTS = [str(root)]
                config.TOOL_FILE_BLOCKED_GLOBS = []
                config.TOOL_FILE_MODE = "ask"

                def approve_and_change(_request):
                    note.write_text("changed beta", encoding="utf-8")
                    return True

                llm.set_file_edit_approval_callback(approve_and_change)
                result = llm._execute_model_tool(
                    "edit_file",
                    {"path": str(note), "old": "beta", "new": "gamma"},
                    allowed_tools=["edit_file"],
                )

                self.assertIn("changed after approval", result)
                self.assertEqual(note.read_text(encoding="utf-8"), "changed beta")
        finally:
            config.TOOL_FILE_ROOTS = old_roots
            config.TOOL_FILE_MODE = old_mode
            config.TOOL_FILE_BLOCKED_GLOBS = old_blocked
            llm.set_file_edit_approval_callback(None)

    def test_codex_responses_tool_loop_can_write_local_file(self):
        """Verify ChatGPT Responses function calls execute local file tools."""
        old_roots = getattr(config, "TOOL_FILE_ROOTS", [])
        old_blocked = getattr(config, "TOOL_FILE_BLOCKED_GLOBS", [])
        llm.set_live_file_access_mode("auto")
        try:
            with TemporaryDirectory() as tmp:
                root = Path(tmp)
                config.TOOL_FILE_ROOTS = [str(root)]
                config.TOOL_FILE_BLOCKED_GLOBS = []

                first = SimpleNamespace(
                    id="resp_1",
                    output_text="",
                    output=[
                        SimpleNamespace(
                            type="function_call",
                            call_id="call_1",
                            name="write_file",
                            arguments='{"path":"whatever.txt","content":"Whatever"}',
                        )
                    ],
                )
                second = SimpleNamespace(id="resp_2", output_text="Created whatever.txt.", output=[])

                class Responses:
                    def __init__(self):
                        self.calls = []

                    def create(self, **kwargs):
                        self.calls.append(kwargs)
                        return first if len(self.calls) == 1 else second

                client = SimpleNamespace(responses=Responses())

                text = "".join(
                    llm._stream_codex(
                        "make one text file",
                        "gpt-test",
                        client,
                        use_tools=True,
                        allowed_tools=["write_file", "read_file", "list_files"],
                        pinned_tools=["write_file"],
                    )
                )

                self.assertEqual(text, "Created whatever.txt.")
                self.assertEqual((root / "whatever.txt").read_text(encoding="utf-8"), "Whatever")
                self.assertIn("tools", client.responses.calls[0])
                self.assertEqual(client.responses.calls[1]["previous_response_id"], "resp_1")
        finally:
            config.TOOL_FILE_ROOTS = old_roots
            config.TOOL_FILE_BLOCKED_GLOBS = old_blocked
            llm.set_live_file_access_mode(None)


if __name__ == "__main__":
    unittest.main()
