"""Regression tests for intent overlay key swallowing."""

from __future__ import annotations

import ast
from pathlib import Path


def test_windows_intent_keyboard_hook_suppresses_underlying_app_keys():
    """Verify raw picker keys are swallowed before they reach the focused app."""
    tree = ast.parse(Path("ui/intent_overlay.py").read_text(encoding="utf-8-sig"))

    def is_suppressed_on_press(node: ast.AST) -> bool:
        if not isinstance(node, ast.Call):
            return False
        if not isinstance(node.func, ast.Attribute) or node.func.attr != "on_press":
            return False
        return any(
            keyword.arg == "suppress"
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value is True
            for keyword in node.keywords
        )

    assert any(is_suppressed_on_press(node) for node in ast.walk(tree))
