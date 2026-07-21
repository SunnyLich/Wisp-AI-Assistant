"""Tests for test context fetcher macos."""

import importlib
import sys
import types
import unittest
from unittest.mock import patch


class MacContextFetcherDocumentTests(unittest.TestCase):
    def setUp(self):
        self._platform_patch = patch.object(sys, "platform", "darwin")
        self._platform_patch.start()
        import core.context_fetcher as context_fetcher
        self.cf = importlib.reload(context_fetcher)

    def tearDown(self):
        self._platform_patch.stop()
        import core.context_fetcher as context_fetcher
        importlib.reload(context_fetcher)

    def test_enumerate_open_doc_windows_uses_macos_helper(self):
        rows = [
            {"process_name": "TextEdit", "pid": 101, "frontmost": True, "title": "Notes.txt - TextEdit"},
            {"process_name": "Finder", "pid": 202, "frontmost": False, "title": "Downloads"},
        ]

        def fake_process(pid: int):
            """Return a process name matching the fake macOS window owner."""
            names = {101: "TextEdit", 202: "Finder"}
            return types.SimpleNamespace(name=lambda: names.get(pid, ""))

        fake_psutil = types.SimpleNamespace(Process=fake_process)

        with patch("core.platform.macos_native.list_document_windows", return_value=rows), \
             patch.dict(sys.modules, {"psutil": fake_psutil}):
            wins = self.cf._enumerate_open_doc_windows()

        self.assertEqual(len(wins), 1)
        self.assertEqual(wins[0].title, "Notes.txt - TextEdit")
        self.assertEqual(wins[0].process_name, "TextEdit")
        self.assertEqual(wins[0].pid, 101)

    def test_resolve_doc_path_matches_open_file_from_lsof(self):
        win = self.cf.WindowInfo(title="Notes.txt - TextEdit", process_name="TextEdit", pid=101)
        lsof_result = types.SimpleNamespace(
            returncode=0,
            stdout="p101\nn/Users/test/Library/Caches/ignore.tmp\nn/Users/test/Documents/Notes.txt\n",
            stderr="",
        )

        def _isfile(path):
            return path == "/Users/test/Documents/Notes.txt"

        with patch("subprocess.run", return_value=lsof_result), \
             patch.object(self.cf.os.path, "isfile", side_effect=_isfile):
            resolved = self.cf._resolve_doc_path(win)

        self.assertEqual(resolved, "/Users/test/Documents/Notes.txt")

    def test_fetch_active_window_uses_frontmost_macos_window(self):
        rows = [
            {"process_name": "Finder", "pid": 202, "frontmost": False, "title": "Downloads"},
            {"process_name": "TextEdit", "pid": 101, "frontmost": True, "title": "Notes.txt"},
        ]

        with patch("core.platform.macos_native.list_document_windows", return_value=rows):
            win = self.cf._fetch_active_window()

        self.assertEqual(win.title, "Notes.txt")
        self.assertEqual(win.process_name, "TextEdit")
        self.assertEqual(win.pid, 101)

    def test_browser_window_scan_finds_background_safari(self):
        """Verify Browser/Web scans visible browsers even when a document is frontmost."""
        rows = [
            {"process_name": "TextEdit", "pid": 101, "frontmost": True, "title": "Notes.txt"},
            {"process_name": "Safari", "pid": 303, "frontmost": False, "title": "Example Page"},
        ]

        with patch("core.platform.macos_native.list_document_windows", return_value=rows), \
             patch.object(self.cf, "_mac_browser_url", return_value="https://example.test/page"):
            win = self.cf.get_browser_window_for_context()

        self.assertEqual(win.process_name, "Safari")
        self.assertEqual(win.pid, 303)
        self.assertEqual(win.title, "Example Page")
        self.assertEqual(win.url, "https://example.test/page")

    def test_plain_textedit_title_resolves_open_file_from_lsof(self):
        win = self.cf.WindowInfo(title="Notes.txt", process_name="TextEdit", pid=101)
        lsof_result = types.SimpleNamespace(
            returncode=0,
            stdout="p101\nn/Users/test/Documents/Notes.txt\n",
            stderr="",
        )

        def _isfile(path):
            return path == "/Users/test/Documents/Notes.txt"

        with patch("subprocess.run", return_value=lsof_result), \
             patch.object(self.cf.os.path, "isfile", side_effect=_isfile):
            resolved = self.cf._resolve_doc_path(win)

        self.assertEqual(resolved, "/Users/test/Documents/Notes.txt")


if __name__ == "__main__":
    unittest.main()
