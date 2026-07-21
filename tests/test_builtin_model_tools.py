"""Tests for test builtin model tools."""

import json
import unittest
import urllib.error
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import config
from core import context_fetcher
from core.llm_clients import client as llm
from core.llm_clients import prompt_guidance
from core.settings_model import ToolTurnBudgets


class BuiltinModelToolsTests(unittest.TestCase):
    _GIT_TOOLS = {"git_status", "git_diff", "github_repo", "github_issue"}

    def test_context_size_limits_reach_browser_ambient_and_tool_readers(self):
        """Each configured limit must reach the production reader it governs."""

        response = SimpleNamespace(text="<html><body>browser text</body></html>")
        response.raise_for_status = lambda: None
        with (
            patch.object(config, "CONTEXT_BROWSER_MAX_CHARS", 111),
            patch("requests.get", return_value=response),
            patch.object(
                context_fetcher,
                "extract_useful_page_context",
                return_value="browser text",
            ) as extract_page,
        ):
            self.assertEqual(
                context_fetcher._fetch_browser_content("https://example.test/page"),
                "browser text",
            )
        self.assertEqual(extract_page.call_args.kwargs["max_chars"], 111)

        active = context_fetcher.WindowInfo(
            title="note.txt - Notepad",
            process_name="notepad.exe",
            pid=7,
            hwnd=8,
        )
        with (
            patch.object(config, "CONTEXT_AMBIENT_DOCUMENT_MAX_CHARS", 222),
            patch.object(context_fetcher, "_IS_WIN", True),
            patch.object(context_fetcher, "_extract_doc_name_from_window", return_value="note.txt"),
            patch.object(context_fetcher, "_enumerate_open_doc_windows", return_value=[]),
            patch.object(
                context_fetcher,
                "_read_window_document_text",
                return_value=("ambient text", "uia"),
            ) as read_window,
        ):
            documents, _debug = context_fetcher.get_all_open_document_window_texts_with_debug(
                active_window=active
            )
        self.assertEqual(documents, [("note.txt", "ambient text")])
        self.assertEqual(read_window.call_args.args[1], 222)

        with (
            patch.object(
                config,
                "get_settings",
                return_value=SimpleNamespace(
                    context=SimpleNamespace(tool_document_max_chars=333)
                ),
            ),
            patch.object(context_fetcher, "get_all_open_document_paths", return_value=["note.txt"]),
            patch.object(llm, "_read_document_paths", return_value="tool text") as read_paths,
        ):
            self.assertEqual(llm._execute_get_context({}), "tool text")
        self.assertEqual(read_paths.call_args.kwargs["max_chars_per_doc"], 333)

    def test_git_and_github_tools_are_registered(self):
        names = {schema["name"] for schema in llm._TOOL_REGISTRY.schemas()}

        self.assertTrue(self._GIT_TOOLS <= names)
        self.assertIn("retrieve_website", names)

    def test_git_and_github_tools_surface_for_relevant_prompt(self):
        """Verify git and github tools surface without prompt keyword routing."""
        empty = {schema["name"] for schema in llm._get_tool_schemas("")}
        self.assertTrue(self._GIT_TOOLS <= empty)

        relevant = {
            schema["name"]
            for schema in llm._get_tool_schemas(
                "show me the git status and git diff, and the github repo and issue"
            )
        }
        self.assertTrue(self._GIT_TOOLS <= relevant)

    def test_model_listing_failure_matrix_is_in_band(self):
        """Model-list refresh faults keep Settings on its curated fallback list."""
        models, error = llm.safe_list_models("chatgpt")
        self.assertEqual(models, [])
        self.assertIn("does not support model listing", error)

        with patch.object(llm.config, "OPENAI_API_KEY", ""):
            models, error = llm.safe_list_models("openai")
        self.assertEqual(models, [])
        self.assertIn("No OpenAI API key", error)

        faults = (
            ConnectionError("provider API is offline"),
            RuntimeError("provider API request is rate-limited"),
        )
        for fault in faults:
            fake = SimpleNamespace(models=SimpleNamespace(list=lambda: (_ for _ in ()).throw(fault)))
            with self.subTest(failure=str(fault)), patch.object(
                llm.sdk_clients,
                "openai_client",
                return_value=fake,
            ):
                models, error = llm.safe_list_models("openai", api_key="test-key")
            self.assertEqual(models, [])
            self.assertIn(str(fault), error)

        changed = SimpleNamespace(models=SimpleNamespace(list=lambda: SimpleNamespace(items=[])))
        with patch.object(llm.sdk_clients, "openai_client", return_value=changed):
            models, error = llm.safe_list_models("openai", api_key="test-key")
        self.assertEqual(models, [])
        self.assertIn("AttributeError", error)

    def test_github_context_failure_matrix_is_controlled(self):
        """Exercise every GitHub-context inventory cause at the real tool adapter."""
        with patch("core.auth.github.get_valid_access_token", return_value=""):
            self.assertIn("not configured", llm._execute_github_repo({"repo": "owner/repo"}))

        self.assertIn("Invalid repo", llm._execute_github_repo({"repo": "not a repo"}))
        self.assertIn(
            "Invalid GitHub issue",
            llm._execute_github_issue({"repo": "owner/repo", "number": "../private"}),
        )

        failures = (
            (401, "authentication is invalid"),
            (403, "required scope"),
            (404, "does not exist"),
            (503, "API is unavailable"),
        )
        with patch("core.auth.github.get_valid_access_token", return_value="token"):
            for status, expected in failures:
                with self.subTest(status=status), patch(
                    "urllib.request.urlopen",
                    side_effect=urllib.error.HTTPError(
                        "https://api.github.test/resource",
                        status,
                        "failure",
                        {},
                        None,
                    ),
                ):
                    result = llm._execute_github_repo({"repo": "owner/repo"})
                    self.assertIn(expected, result)

            with patch(
                "urllib.request.urlopen",
                side_effect=urllib.error.URLError("network unavailable"),
            ):
                result = llm._execute_github_issue({"repo": "owner/repo", "number": 7})
                self.assertIn("network request failed", result)

            response = SimpleNamespace(
                __enter__=lambda self: self,
                __exit__=lambda self, *_args: False,
                read=lambda: b"not-json",
            )
            with patch("urllib.request.urlopen", return_value=response):
                result = llm._execute_github_repo({"repo": "owner/repo"})
                self.assertIn("returned invalid data", result)

    def test_git_inspection_failure_matrix_is_controlled(self):
        """Exercise local Git faults at the shared status/diff command adapter."""
        completed = SimpleNamespace(returncode=128, stdout="", stderr="not a git repository")
        with TemporaryDirectory() as folder, patch(
            "subprocess.run", return_value=completed
        ) as run:
            result = llm._execute_git_status({"cwd": folder})
        self.assertIn("not a git repository", result)
        self.assertEqual(run.call_args.args[0], ["git", "status", "--short", "--", "."])

        with TemporaryDirectory() as folder, patch(
            "subprocess.run", side_effect=FileNotFoundError
        ):
            self.assertIn("Git is unavailable", llm._execute_git_diff({"cwd": folder}))

        with TemporaryDirectory() as folder, patch(
            "subprocess.run", side_effect=PermissionError("access denied")
        ):
            self.assertIn("cannot be read", llm._execute_git_status({"cwd": folder}))

        with TemporaryDirectory() as folder, patch(
            "subprocess.run",
            return_value=SimpleNamespace(returncode=0, stdout="x" * 12001, stderr=""),
        ) as run:
            result = llm._execute_git_diff({"cwd": folder})
        self.assertIn("truncated at 12000", result)
        self.assertEqual(run.call_args.args[0], ["git", "diff", "--", "."])
        self.assertEqual(Path(run.call_args.kwargs["cwd"]), Path(folder).resolve())

        missing = Path(folder) / "removed-before-inspection"
        self.assertIn("working folder is unavailable", llm._execute_git_status({"cwd": missing}))

    def test_git_and_github_tools_return_successful_runtime_results(self):
        """Exercise all four production adapters on their successful result path."""

        git_results = iter(
            (
                SimpleNamespace(returncode=0, stdout=" M changed.py\n", stderr=""),
                SimpleNamespace(returncode=0, stdout="+added line\n", stderr=""),
            )
        )
        with TemporaryDirectory() as folder, patch("subprocess.run", side_effect=lambda *_a, **_k: next(git_results)):
            self.assertIn("changed.py", llm._execute_git_status({"cwd": folder}))
            self.assertIn("added line", llm._execute_git_diff({"cwd": folder}))

        payloads = iter(
            (
                {"full_name": "owner/repo", "private": False, "default_branch": "main"},
                {"number": 7, "title": "Fix runtime", "state": "open"},
            )
        )

        class Response:
            def __enter__(self):
                self.payload = next(payloads)
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps(self.payload).encode("utf-8")

        with patch("core.auth.github.get_valid_access_token", return_value="token"), patch(
            "urllib.request.urlopen", side_effect=lambda *_a, **_k: Response()
        ) as fetch:
            repo = llm._execute_github_repo({"repo": "owner/repo"})
            issue = llm._execute_github_issue({"repo": "owner/repo", "number": 7})

        self.assertIn('"full_name": "owner/repo"', repo)
        self.assertIn('"title": "Fix runtime"', issue)
        self.assertEqual(fetch.call_count, 2)

    def test_allowed_tool_filter_limits_general_schemas(self):
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

    def test_tool_result_budget_clips_large_outputs(self):
        """Verify active profile tool-result budgets cap returned text."""
        settings = SimpleNamespace(
            tool_turn=ToolTurnBudgets(
                max_calls=3,
                max_result_chars=10,
                max_total_chars=12,
            )
        )

        with patch.object(llm.config, "get_settings", return_value=settings):
            first, spent = llm._clip_tool_result_for_turn("abcdefghijklmno", 0)
            second, spent = llm._clip_tool_result_for_turn("uvwxyz", spent)

        self.assertTrue(first.startswith("abcdefghij"))
        self.assertIn("truncated", first)
        self.assertTrue(second.startswith("uv"))
        self.assertIn("truncated", second)
        self.assertEqual(spent, 12)

    def test_shared_tool_policy_and_budget_failure_matrix_is_controlled(self):
        """Exercise reusable tool gates for screen, memory, and approval workflows."""
        for name in ("capture_screen", "memory_save"):
            with self.subTest(tool=name, failure="disabled"):
                result = llm._execute_model_tool(name, {}, allowed_tools=[])
                self.assertIn("disabled", result)

            with self.subTest(tool=name, failure="scope"):
                result = llm._execute_model_tool(
                    name,
                    {},
                    allowed_tools=["get_context.documents"],
                )
                self.assertIn("disabled", result)

            with self.subTest(tool=name, failure="invalid inputs"):
                result = llm._execute_model_tool(name, [], allowed_tools=[name])
                self.assertIn("Invalid inputs", result)

        with patch(
            "core.capture.get_screen_snippet",
            side_effect=PermissionError("screen recording permission denied"),
        ):
            self.assertIsNone(llm._capture_screen_b64())

        with patch(
            "core.memory_store.store.get_manager",
            side_effect=PermissionError("memory store permission denied"),
        ):
            denied = llm._execute_model_tool(
                "memory_save",
                {"text": "I prefer concise answers."},
                allowed_tools=["memory_save"],
            )
        self.assertIn("PermissionError", denied)

        settings = SimpleNamespace(
            tool_turn=ToolTurnBudgets(max_calls=0, max_result_chars=5, max_total_chars=5)
        )
        with patch.object(llm.config, "get_settings", return_value=settings):
            self.assertTrue(llm._tool_call_limit_reached(0))
            clipped, spent = llm._clip_tool_result_for_turn("too much output", 0)
            exhausted, unchanged = llm._clip_tool_result_for_turn("more", spent)

        self.assertIn("truncated", clipped)
        self.assertEqual(spent, 5)
        self.assertIn("budget is exhausted", exhausted)
        self.assertEqual(unchanged, spent)

    def test_retrieve_website_is_browser_scoped(self):
        """Verify retrieve_website follows explicit Browser/Web tool grants."""
        names = {
            schema["name"]
            for schema in llm._get_tool_schemas(
                "read this website",
                allowed_tools=["retrieve_website"],
                pinned_tools=["retrieve_website"],
            )
        }
        blocked = llm._execute_model_tool(
            "retrieve_website",
            {"url": "https://example.com"},
            allowed_tools=["get_context.documents"],
        )

        self.assertIn("retrieve_website", names)
        self.assertIn("disabled", blocked)
        with patch(
            "core.context_fetcher.fetch_browser_content_for_tool",
            return_value="Retrieved page body",
        ) as fetch:
            result = llm._execute_model_tool(
                "retrieve_website",
                {"url": "https://example.test/page"},
                allowed_tools=["retrieve_website"],
            )
        fetch.assert_called_once_with("https://example.test/page")
        self.assertEqual(result, "Retrieved page body")

    def test_web_context_failure_matrix_is_controlled(self):
        """Exercise every browser/web inventory cause through shared tool boundaries."""
        disabled = llm._execute_model_tool(
            "retrieve_website",
            {"url": "https://example.test"},
            allowed_tools=[],
        )
        self.assertIn("disabled", disabled)

        with patch("core.context_fetcher.fetch_browser_content_for_tool", return_value=""):
            blocked = llm._execute_model_tool(
                "retrieve_website",
                {"url": "https://blocked.example"},
                allowed_tools=["retrieve_website"],
            )
        self.assertIn("Could not fetch content", blocked)

        with patch(
            "core.context_fetcher.fetch_browser_content_for_tool",
            side_effect=OSError("network access unavailable"),
        ):
            offline = llm._execute_model_tool(
                "retrieve_website",
                {"url": "https://offline.example"},
                allowed_tools=["retrieve_website"],
            )
        self.assertIn("network access unavailable", offline)

        with patch("core.context_fetcher.get_all_open_document_paths", return_value=[]):
            missing = llm._execute_model_tool(
                "get_context",
                {},
                allowed_tools=["get_context.documents"],
            )
        self.assertIn("Could not determine", missing)

        with patch(
            "core.context_fetcher.fetch_browser_content_for_tool",
            return_value={"unexpected": "shape"},
        ):
            changed = llm._execute_model_tool(
                "retrieve_website",
                {"url": "https://changed.example"},
                allowed_tools=["retrieve_website"],
            )
        self.assertIn("unsupported format", changed)

        settings = SimpleNamespace(
            tool_turn=ToolTurnBudgets(max_calls=3, max_result_chars=25, max_total_chars=25)
        )
        with patch.object(llm.config, "get_settings", return_value=settings):
            clipped, spent = llm._clip_tool_result_for_turn("page" * 100, 0)
        self.assertIn("truncated", clipped)
        self.assertEqual(spent, 25)

    def test_memory_search_is_opt_in(self):
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
        manager = SimpleNamespace(retrieve_relevant=lambda query, top_k=None: f"memory for {query} ({top_k})")
        with patch("core.memory_store.store.get_manager", return_value=manager):
            result = llm._execute_model_tool(
                "memory_search",
                {"query": "answer style", "top_k": 2},
                allowed_tools=["memory_search"],
            )
        self.assertEqual(result, "memory for answer style (2)")

    def test_memory_search_on_demand_failure_matrix_is_controlled(self):
        """Keep optional memory retrieval safe across policy, store, and budget faults."""
        disabled = llm._execute_model_tool(
            "memory_search",
            {"query": "answer style"},
            allowed_tools=[],
        )
        scoped_out = llm._execute_model_tool(
            "memory_search",
            {"query": "answer style"},
            allowed_tools=["get_context.documents"],
        )
        invalid = llm._execute_model_tool(
            "memory_search",
            {},
            allowed_tools=["memory_search"],
        )
        with patch(
            "core.memory_store.store.get_manager",
            side_effect=OSError("memory store is unavailable"),
        ):
            unavailable = llm._execute_model_tool(
                "memory_search",
                {"query": "answer style"},
                allowed_tools=["memory_search"],
            )

        settings = SimpleNamespace(
            tool_turn=ToolTurnBudgets(max_calls=0, max_result_chars=8, max_total_chars=8)
        )
        with patch.object(llm.config, "get_settings", return_value=settings):
            self.assertTrue(llm._tool_call_limit_reached(0))
            clipped, spent = llm._clip_tool_result_for_turn("remembered fact" * 10, 0)
            exhausted, _ = llm._clip_tool_result_for_turn("another fact", spent)

        self.assertIn("disabled", disabled)
        self.assertIn("disabled", scoped_out)
        self.assertIn("missing required", invalid)
        self.assertIn("memory store is unavailable", unavailable)
        self.assertIn("truncated", clipped)
        self.assertIn("budget is exhausted", exhausted)

    def test_memory_search_note_only_when_tool_offered(self):
        base = "You are a concise desktop assistant."

        self.assertIn("memory_search tool", llm._with_memory_search_note(base, ["memory_search"]))
        self.assertEqual(llm._with_memory_search_note(base, ["memory_save"]), base)
        self.assertEqual(llm._with_memory_search_note(base, None), base)

    def test_prompt_guidance_builds_query_notes_from_one_place(self):
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
        captured = {}

        class FakeManager:
            """Coordinate fake manager behavior."""
            def retrieve_relevant(self, query):
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
        with patch("core.memory_store.store.get_manager") as get_manager:
            ambient = llm._inject_frontloaded_tool_context(
                "Active app: Notes",
                [],
                query="what do you remember?",
            )

        self.assertEqual(ambient, "Active app: Notes")
        get_manager.assert_not_called()

    def test_get_context_execution_respects_source_allowlist(self):
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
        with patch("core.context_fetcher.get_all_open_document_paths", return_value=["notes.txt"]), patch.object(
            llm, "_read_document_paths", return_value="Open document body"
        ):
            documents = llm._execute_model_tool(
                "get_context",
                {},
                allowed_tools=["get_context.documents"],
            )
        with patch(
            "core.context_fetcher.fetch_browser_content_for_tool",
            return_value="Current browser body",
        ):
            browser = llm._execute_model_tool(
                "get_context",
                {"url": "https://example.test/current"},
                allowed_tools=["get_context.browser"],
            )
        self.assertEqual(documents, "Open document body")
        self.assertEqual(browser, "Current browser body")

    def test_pinned_tools_bypass_keyword_filter(self):
        """Verify allowed tools do not depend on prompt wording."""
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

        self.assertIn("git_status", filtered)
        self.assertIn("git_status", pinned)

    def test_pinned_tools_bypass_keyword_filter_openai_format(self):
        """Verify allowed tools do not depend on prompt wording in OpenAI format."""
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

    def test_pinned_browser_mode_openai_offers_web_and_context_functions(self):
        schemas = llm._get_openai_tool_schemas(
            "hello",
            allowed_tools=["web_search", "get_context.browser"],
            pinned_tools=["web_search", "get_context"],
        )
        names = {
            (schema.get("function") or {}).get("name")
            for schema in schemas
        }
        web_schema = next(
            schema for schema in schemas
            if (schema.get("function") or {}).get("name") == "web_search"
        )

        self.assertIn("get_context", names)
        self.assertIn("web_search", names)
        self.assertIn(
            "query",
            ((web_schema.get("function") or {}).get("parameters") or {}).get("required", []),
        )

    def test_web_search_executes_local_fallback(self):
        """Verify web_search returns source-bearing results on local fallback routes."""
        with patch(
            "core.context_fetcher.search_online_for_tool",
            return_value=[
                {
                    "title": "Example News",
                    "url": "https://example.test/news",
                    "snippet": "Fresh headline summary.",
                }
            ],
        ) as search:
            result = llm._execute_model_tool(
                "web_search",
                {"query": "today's news", "max_results": 3},
                allowed_tools=["web_search"],
            )

        search.assert_called_once_with("today's news", max_results=3)
        self.assertIn("Example News", result)
        self.assertIn("https://example.test/news", result)
        self.assertIn("Fresh headline summary.", result)

    def test_pinned_opt_in_tools_are_not_added(self):
        # capture_screen is governed by the screenshot setting, never by the
        # per-caller tool list, even if someone hand-writes it into the env.
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
        self.assertIn("edit_file", irrelevant_names)
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

                created = root / "hello.py"
                result = llm._execute_model_tool(
                    "create_file",
                    {"path": str(created), "content": 'print("Hello, world!")\n'},
                    allowed_tools=["create_file"],
                )

                self.assertIn("hello.py", result)
                self.assertEqual(created.read_text(encoding="utf-8"), 'print("Hello, world!")\n')
                self.assertIn(
                    "failed",
                    llm._execute_model_tool(
                        "create_file",
                        {"path": str(created), "content": "again\n"},
                        allowed_tools=["create_file"],
                    ),
                )
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

    def test_codex_tool_prompt_names_allowed_local_file_root(self):
        """Verify file-tool prompts tell the model which local folder is allowed."""
        old_roots = getattr(config, "TOOL_FILE_ROOTS", [])
        old_blocked = getattr(config, "TOOL_FILE_BLOCKED_GLOBS", [])
        llm.set_live_file_access_mode("ask")
        try:
            with TemporaryDirectory() as tmp:
                root = Path(tmp)
                config.TOOL_FILE_ROOTS = [str(root)]
                config.TOOL_FILE_BLOCKED_GLOBS = []

                class Responses:
                    def __init__(self):
                        self.calls = []

                    def create(self, **kwargs):
                        self.calls.append(kwargs)
                        return SimpleNamespace(id="resp_1", output_text="ok", output=[])

                responses = Responses()
                client = SimpleNamespace(responses=responses)

                text = "".join(
                    llm._stream_codex(
                        "update hello_world.py",
                        "gpt-test",
                        client,
                        use_tools=True,
                        allowed_tools=["create_file", "edit_file", "write_file", "read_file"],
                        pinned_tools=["create_file", "edit_file", "write_file", "read_file"],
                    )
                )

                self.assertEqual(text, "ok")
                instructions = responses.calls[0]["instructions"]
                self.assertIn("Local file access is available", instructions)
                for configured_root in llm.configured_file_roots():
                    self.assertIn(str(configured_root), instructions)
                self.assertIn("complete the requested operation", instructions)
                self.assertIn("reading a file is not a substitute", instructions)
        finally:
            config.TOOL_FILE_ROOTS = old_roots
            config.TOOL_FILE_BLOCKED_GLOBS = old_blocked
            llm.set_live_file_access_mode(None)

    def test_codex_tool_loop_marks_pre_tool_text_as_progress(self):
        """Verify Responses pre-tool text streams as progress narration."""
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
                    output_text="Checking the file first.",
                    output=[
                        SimpleNamespace(
                            type="function_call",
                            call_id="call_1",
                            name="create_file",
                            arguments='{"path":"note.txt","content":"hello"}',
                        )
                    ],
                )
                final = SimpleNamespace(id="resp_2", output_text="Created note.txt.", output=[])

                class Responses:
                    def __init__(self):
                        self.calls = []

                    def create(self, **kwargs):
                        self.calls.append(kwargs)
                        return first if len(self.calls) == 1 else final

                client = SimpleNamespace(responses=Responses())

                chunks = list(
                    llm._stream_codex(
                        "create note.txt",
                        "gpt-test",
                        client,
                        use_tools=True,
                        allowed_tools=["create_file"],
                        pinned_tools=["create_file"],
                    )
                )

                self.assertEqual(chunks, ["Checking the file first.", "Created note.txt."])
                self.assertEqual(getattr(chunks[0], "kind", ""), "progress")
                self.assertEqual((root / "note.txt").read_text(encoding="utf-8"), "hello")
        finally:
            config.TOOL_FILE_ROOTS = old_roots
            config.TOOL_FILE_BLOCKED_GLOBS = old_blocked
            llm.set_live_file_access_mode(None)

    def test_responses_reasoning_summary_deltas_stream_as_thoughts(self):
        """Verify Responses reasoning deltas are surfaced as thought chunks."""
        events = [
            SimpleNamespace(type="response.reasoning_summary_text.delta", delta="Thinking "),
            SimpleNamespace(type="response.reasoning_text.delta", delta="through it."),
            SimpleNamespace(type="response.output_text.delta", delta="Done."),
        ]

        class FakeStream:
            def __enter__(self):
                return iter(events)

            def __exit__(self, exc_type, exc, tb):
                return False

        class FakeResponses:
            def stream(self, **_kwargs):
                return FakeStream()

        chunks = list(
            llm._response_stream_text(
                SimpleNamespace(responses=FakeResponses()),
                {"model": "gpt-test", "input": "hi"},
                provider="chatgpt",
                model="gpt-test",
            )
        )

        self.assertEqual(chunks, ["Thinking ", "through it.", "Done."])
        self.assertEqual([getattr(chunk, "kind", "answer") for chunk in chunks], ["thought", "thought", "answer"])

    def test_responses_chat_requests_configured_reasoning_effort(self):
        """Verify Responses calls request the configured chat reasoning effort."""
        old_effort = getattr(config, "CHAT_REASONING_EFFORT", "")
        try:
            config.CHAT_REASONING_EFFORT = "high"

            class FakeResponses:
                def __init__(self):
                    self.calls = []

                def create(self, **kwargs):
                    self.calls.append(kwargs)
                    return SimpleNamespace(id="resp_1", output_text="Done.", output=[])

            responses = FakeResponses()
            response = llm._responses_create_with_retries(
                SimpleNamespace(responses=responses),
                {"model": "gpt-test", "input": "hi"},
                provider="chatgpt",
                model="gpt-test",
            )

            self.assertEqual(response.output_text, "Done.")
            self.assertEqual(responses.calls[0]["reasoning"], {"effort": "high"})
        finally:
            config.CHAT_REASONING_EFFORT = old_effort

    def test_chatgpt_stream_required_error_wording_is_detected(self):
        """Verify ChatGPT stream-required wording is treated as recoverable."""
        exc = Exception("stream isn't true")

        self.assertTrue(llm._requires_stream_error(exc))
        self.assertTrue(llm._stream_mode_error(exc))

    def test_chatgpt_responses_stream_sets_explicit_stream_body(self):
        """Verify Responses streaming sends stream=true for ChatGPT Codex."""
        calls = []

        class StreamContext:
            """Minimal Responses stream context manager."""

            def __enter__(self):
                """Enter the fake stream."""
                return iter([SimpleNamespace(type="response.output_text.delta", delta="OK")])

            def __exit__(self, *_args):
                """Exit the fake stream."""
                return False

        class Responses:
            """Fake Responses API."""

            def stream(self, **kwargs):
                """Record streaming kwargs."""
                calls.append(kwargs)
                return StreamContext()

        client = SimpleNamespace(responses=Responses())
        text = "".join(
            llm._response_stream_text(
                client,
                {
                    "model": "gpt-5.5-test",
                    "input": [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
                    "store": False,
                },
                provider="chatgpt",
                model="gpt-5.5-test",
            )
        )

        self.assertEqual(text, "OK")
        self.assertEqual(calls[0]["extra_body"]["stream"], True)


if __name__ == "__main__":
    unittest.main()
