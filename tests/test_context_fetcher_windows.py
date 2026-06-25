"""Tests for test context fetcher windows."""

import importlib
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch


class WindowsContextFetcherDocumentTests(unittest.TestCase):
    """Test case for windows context fetcher document tests behavior."""
    def setUp(self):
        """Verify set up behavior."""
        self._platform_patch = patch.object(sys, "platform", "win32")
        self._platform_patch.start()
        import core.context_fetcher as context_fetcher
        self.cf = importlib.reload(context_fetcher)

    def tearDown(self):
        """Verify tear down behavior."""
        self._platform_patch.stop()
        import core.context_fetcher as context_fetcher
        importlib.reload(context_fetcher)

    def test_resolve_doc_path_matches_open_file_from_process(self):
        """Verify resolve doc path matches open file from process behavior."""
        win = self.cf.WindowInfo(
            title="Notes.txt - Notepad",
            process_name="notepad.exe",
            pid=101,
        )

        with patch.object(
            self.cf,
            "_win_open_files_for_pid",
            return_value=[r"C:\Users\sunny\Documents\Notes.txt"],
        ), patch.object(self.cf, "_fetch_recent_files", return_value=[]):
            resolved = self.cf._resolve_doc_path(win)

        self.assertEqual(resolved, r"C:\Users\sunny\Documents\Notes.txt")

    def test_resolve_doc_path_strips_modified_marker(self):
        """Verify resolve doc path strips modified marker behavior."""
        win = self.cf.WindowInfo(
            title="*Notes.txt - Notepad",
            process_name="notepad.exe",
            pid=101,
        )

        with patch.object(
            self.cf,
            "_win_open_files_for_pid",
            return_value=[r"C:\Users\sunny\Documents\Notes.txt"],
        ), patch.object(self.cf, "_fetch_recent_files", return_value=[]):
            resolved = self.cf._resolve_doc_path(win)

        self.assertEqual(resolved, r"C:\Users\sunny\Documents\Notes.txt")

    def test_extract_doc_name_supports_localized_notepad_title(self):
        """Verify extract doc name supports localized notepad title behavior."""
        win = self.cf.WindowInfo(
            title="Summary.txt - 記事本",
            process_name="notepad.exe",
            pid=101,
        )

        self.assertEqual(self.cf._extract_doc_name_from_window(win), "Summary.txt")

    def test_extract_doc_name_uses_office_process_when_title_suffix_is_localized(self):
        """Verify extract doc name uses office process when title suffix is localized behavior."""
        win = self.cf.WindowInfo(
            title="Budget.xlsx - Excel Localized",
            process_name="EXCEL.EXE",
            pid=101,
        )

        self.assertEqual(self.cf._extract_doc_name_from_window(win), "Budget.xlsx")

    def test_extract_doc_name_uses_pdf_process_when_title_suffix_is_unknown(self):
        """Verify extract doc name uses pdf process when title suffix is unknown behavior."""
        win = self.cf.WindowInfo(
            title="Report.pdf - Acrobat Localized",
            process_name="Acrobat.exe",
            pid=101,
        )

        self.assertEqual(self.cf._extract_doc_name_from_window(win), "Report.pdf")

    def test_process_based_doc_title_resolves_open_pdf_path(self):
        """Verify process based doc title resolves open pdf path behavior."""
        win = self.cf.WindowInfo(
            title="Report - Acrobat Localized",
            process_name="Acrobat.exe",
            pid=101,
        )

        with patch.object(
            self.cf,
            "_win_open_files_for_pid",
            return_value=[r"C:\Users\sunny\Documents\Report.pdf"],
        ), patch.object(self.cf, "_fetch_recent_files", return_value=[]):
            resolved = self.cf._resolve_doc_path(win)

        self.assertEqual(resolved, r"C:\Users\sunny\Documents\Report.pdf")

    def test_pdf_xchange_editor_title_resolves_open_pdf_path_without_extension(self):
        """Verify pdf xchange editor title resolves open pdf path without extension behavior."""
        win = self.cf.WindowInfo(
            title="laptop walmart invoice - PDF-XChange Editor",
            process_name="PXCEditor.exe",
            pid=101,
        )

        with patch.object(
            self.cf,
            "_win_open_files_for_pid",
            return_value=[r"C:\Users\sunny\Documents\laptop walmart invoice.pdf"],
        ), patch.object(self.cf, "_fetch_recent_files", return_value=[]):
            resolved = self.cf._resolve_doc_path(win)

        self.assertEqual(resolved, r"C:\Users\sunny\Documents\laptop walmart invoice.pdf")

    def test_open_document_window_texts_use_localized_notepad_hotkey_window(self):
        """Verify open document window texts use localized notepad hotkey window behavior."""
        self.cf._context_window = self.cf.WindowInfo(
            title="Summary.txt - 記事本",
            process_name="notepad.exe",
            pid=101,
            hwnd=777,
        )

        with patch.object(self.cf, "_enumerate_open_doc_windows", return_value=[]), \
             patch.object(self.cf, "_get_window_text_uia", return_value="hello from localized notepad"):
            docs = self.cf.get_all_open_document_window_texts()

        self.assertEqual(docs, [("Summary.txt", "hello from localized notepad")])

    def test_open_document_paths_prioritize_passed_active_window(self):
        """Verify open document paths prioritize passed active window behavior."""
        self.cf._context_window = self.cf.WindowInfo(
            title="Summary.txt - Notepad",
            process_name="notepad.exe",
            pid=101,
            hwnd=111,
        )
        active = self.cf.WindowInfo(
            title="Budget.xlsx - Excel",
            process_name="EXCEL.EXE",
            pid=202,
            hwnd=222,
        )

        def open_files(pid: int) -> list[str]:
            """Verify open files behavior."""
            if pid == 101:
                return [r"C:\Users\sunny\Documents\Summary.txt"]
            if pid == 202:
                return [r"C:\Users\sunny\Documents\Budget.xlsx"]
            return []

        with patch.object(self.cf, "_win_open_files_for_pid", side_effect=open_files), \
             patch.object(self.cf, "_enumerate_open_doc_windows", return_value=[]), \
             patch.object(self.cf, "_fetch_recent_files", return_value=[]):
            paths = self.cf.get_all_open_document_paths(active_window=active)

        self.assertEqual(paths, [r"C:\Users\sunny\Documents\Budget.xlsx"])

    def test_open_document_window_texts_prioritize_passed_unsaved_calc_window(self):
        """Verify open document window texts prioritize passed unsaved calc window behavior."""
        self.cf._context_window = self.cf.WindowInfo(
            title="Summary.txt - Notepad",
            process_name="notepad.exe",
            pid=101,
            hwnd=111,
        )
        active = self.cf.WindowInfo(
            title="Untitled 1 \u2014 LibreOffice Calc",
            process_name="soffice.bin",
            pid=202,
            hwnd=222,
        )

        with patch.object(self.cf, "_enumerate_open_doc_windows", return_value=[]), \
             patch.object(self.cf, "_get_window_text_uia", return_value="A1\tB1"):
            docs = self.cf.get_all_open_document_window_texts(active_window=active)

        self.assertEqual(docs, [("Untitled 1", "A1\tB1")])

    def test_open_document_window_texts_use_hotkey_window(self):
        """Verify open document window texts use hotkey window behavior."""
        self.cf._context_window = self.cf.WindowInfo(
            title="Notes.txt - Notepad",
            process_name="notepad.exe",
            pid=101,
            hwnd=777,
        )

        with patch.object(self.cf, "_enumerate_open_doc_windows", return_value=[]), \
             patch.object(self.cf, "_get_window_text_uia", return_value="hello from notepad"):
            docs = self.cf.get_all_open_document_window_texts()

        self.assertEqual(docs, [("Notes.txt", "hello from notepad")])

    def test_open_document_window_texts_scan_past_empty_candidates(self):
        """Verify max_docs limits readable results, not candidate windows."""
        self.cf._context_window = self.cf.WindowInfo(
            title="Text1.txt - Notepad",
            process_name="notepad.exe",
            pid=101,
            hwnd=100,
        )
        windows = [
            self.cf.WindowInfo(
                title=f"Empty{i}.txt - Notepad",
                process_name="notepad.exe",
                pid=200 + i,
                hwnd=200 + i,
            )
            for i in range(4)
        ]
        windows.append(
            self.cf.WindowInfo(
                title="Text2.txt - Notepad",
                process_name="notepad.exe",
                pid=300,
                hwnd=300,
            )
        )

        def text_for(hwnd: int, _max_chars: int) -> str:
            if hwnd == 100:
                return "Text1 body"
            if hwnd == 300:
                return "Text2 body"
            return ""

        with patch.object(self.cf, "_enumerate_open_doc_windows", return_value=windows), \
             patch.object(self.cf, "_get_window_text_uia", side_effect=text_for):
            docs = self.cf.get_all_open_document_window_texts(max_docs=2)

        self.assertEqual(docs, [("Text1.txt", "Text1 body"), ("Text2.txt", "Text2 body")])

    def test_code_process_window_is_document_candidate_without_standard_suffix(self):
        """Verify VS Code-like windows are document candidates by process name."""
        win = self.cf.WindowInfo(
            title="demo.py - Python-AI-assistant-overlay",
            process_name="Code.exe",
            pid=202,
            hwnd=222,
        )

        self.assertEqual(self.cf._extract_doc_name_from_window(win), "demo.py")

    def test_code_process_window_without_product_suffix_is_document_candidate(self):
        """Verify bare VS Code tab titles stay eligible for document context."""
        win = self.cf.WindowInfo(
            title="Text 2 \u2022 Untitled-1",
            process_name="Code.exe",
            pid=202,
            hwnd=222,
        )

        self.assertEqual(self.cf._extract_doc_name_from_window(win), "Text 2 \u2022 Untitled-1")

    def test_open_document_window_texts_reads_vscode_untitled_backup_when_uia_empty(self):
        """Verify VS Code unsaved buffers can be read from backup storage."""
        win = self.cf.WindowInfo(
            title="Text 2 \u2022 Untitled-1",
            process_name="Code.exe",
            pid=202,
            hwnd=222,
        )
        with tempfile.TemporaryDirectory() as tmp:
            backup_dir = os.path.join(tmp, "workspace", "untitled")
            os.makedirs(backup_dir)
            with open(os.path.join(backup_dir, "-7f9c1a2e"), "w", encoding="utf-8") as f:
                f.write('untitled:Untitled-1 {"typeId":""}\nText 2\nActual body')

            with patch.object(self.cf, "_enumerate_open_doc_windows", return_value=[win]), \
                 patch.object(self.cf, "_get_window_text_uia", return_value=""), \
                 patch.object(self.cf, "_vscode_backup_root", return_value=tmp):
                docs, debug = self.cf.get_all_open_document_window_texts_with_debug(max_docs=1)

        self.assertEqual(docs, [("Text 2 \u2022 Untitled-1", "Text 2\nActual body")])
        self.assertEqual(debug[0]["method"], "vscode_backup_untitled")

    def test_browser_page_rect_filter_skips_toolbar_area(self):
        """Verify browser page rect filter skips toolbar area behavior."""
        root_rect = (0.0, 0.0, 1280.0, 900.0)
        content_top = self.cf._browser_content_top(root_rect, [(0.0, 112.0, 1280.0, 900.0)])

        self.assertFalse(self.cf._is_probable_page_rect((0.0, 20.0, 1280.0, 80.0), content_top))
        self.assertTrue(self.cf._is_probable_page_rect((0.0, 130.0, 1280.0, 800.0), content_top))

    def test_clean_browser_uia_text_dedupes_short_lines(self):
        """Verify clean browser uia text dedupes short repeated lines behavior."""
        text = "Home\r\nHome\n\nArticle title\n  Body   text  \nArticle title\n"

        cleaned = self.cf._clean_browser_uia_text(text)

        self.assertEqual(cleaned, "Home\n\nArticle title\nBody text")

    def test_repair_mojibake_text_fixes_common_context_garbling(self):
        """Verify fetched context mojibake is repaired before prompt construction."""
        text = "café — 記事本 😊".encode("utf-8").decode("cp1252")

        repaired = self.cf._repair_mojibake_text(text)

        self.assertEqual(repaired, "café — 記事本 😊")

    def test_clean_document_uia_text_trims_vscode_chrome_and_icon_noise(self):
        """Verify document UIA text trims editor chrome leaked by VS Code."""
        text = (
            "This situation should be copied ðŸ˜Š "
            "File Edit Selection View Go Run More \uea60 Search More Actions Open in App\n"
            "This situation should be copied"
        )

        cleaned = self.cf._clean_document_uia_text(text)

        self.assertEqual(cleaned, "This situation should be copied")

    def test_clean_document_uia_text_rejects_vscode_chrome_dump(self):
        """Verify editor menu/status dumps are not sent as document context."""
        text = "\n".join(
            [
                "File",
                "Edit",
                "Selection",
                "View",
                "Go",
                "Run",
                "More",
                "Search",
                "More Actions",
                "Open in Agents",
                "1",
                "Claude Code",
                "Python",
                "GitHub Actions",
                "Text 2Untitled-1",
                "Claude Code: Open",
                (
                    "The editor is not accessible at this time. To enable screen "
                    "reader optimized mode, use Shift+Alt+F1"
                ),
                "0",
                "Plain Text",
                "CRLF",
                "UTF-8",
                "Spaces: 4",
                "Ln 5, Col 82",
            ]
        )

        cleaned = self.cf._clean_document_uia_text(text)

        self.assertEqual(cleaned, "")

    def test_clean_document_uia_text_rejects_captured_editor_sidebar_dump(self):
        """Verify sidebar/tab/search chrome does not become answer context."""
        text = "\n".join(
            [
                "Terminal",
                "Help",
                "Update",
                "README.md",
                "APPCONTEXTCLEANUPTEMPPLAN.md",
                "CHAT/TOOLLOOPCOMPARISONPLAN.md",
                "No results found for 'launch'",
            ]
        )

        cleaned = self.cf._clean_document_uia_text(text)

        self.assertEqual(cleaned, "")

    def test_clean_document_uia_text_keeps_real_text_inside_chrome_dump(self):
        """Verify real editor text survives when standalone chrome is present."""
        text = "\n".join(
            [
                "File",
                "Edit",
                "Selection",
                "This is the actual paragraph from VS Code.",
                "Plain Text",
                "CRLF",
                "UTF-8",
                "Ln 5, Col 82",
            ]
        )

        cleaned = self.cf._clean_document_uia_text(text)

        self.assertEqual(cleaned, "This is the actual paragraph from VS Code.")

    def test_clean_document_uia_text_strips_leading_icon_noise(self):
        """Verify leading editor glyphs are not treated as document content."""
        text = "ðŸ˜Š \uea60 This situation requires immediate attention."

        cleaned = self.cf._clean_document_uia_text(text)

        self.assertEqual(cleaned, "This situation requires immediate attention.")

    def test_clean_document_uia_text_keeps_normal_leading_punctuation(self):
        """Verify ordinary leading punctuation is not stripped as icon noise."""
        text = '"This situation requires immediate attention."'

        cleaned = self.cf._clean_document_uia_text(text)

        self.assertEqual(cleaned, '"This situation requires immediate attention."')

    def test_clean_document_uia_text_keeps_short_plain_document_lines(self):
        """Verify plain content is not stripped without chrome-dump signals."""
        text = "1\nPython"

        cleaned = self.cf._clean_document_uia_text(text)

        self.assertEqual(cleaned, "1\nPython")

    def test_rect_tuple_reads_uia_like_rectangle(self):
        """Verify rect tuple reads uia like rectangle behavior."""
        rect = SimpleNamespace(left=1, top=2, right=3, bottom=4)

        self.assertEqual(self.cf._rect_tuple(rect), (1.0, 2.0, 3.0, 4.0))


if __name__ == "__main__":
    unittest.main()
