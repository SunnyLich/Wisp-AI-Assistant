"""Real-widget acceptance for every built-in intent action."""

from __future__ import annotations

import pytest

from scripts.runtime_test_harness import QtUserDriver

pytestmark = pytest.mark.workflow


def test_every_general_and_rewrite_action_runs_by_key_and_click(qapp) -> None:
    """Open both production pickers and select every built-in row both ways."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    import config
    from ui.intent_overlay import IntentOverlay

    driver = QtUserDriver(qapp, timeout=2.0)
    expected = [
        (caller_idx, row["key"].upper(), row["prompt"])
        for caller_idx in (0, 1)
        for row in config.CALLER_ROWS[caller_idx]["intents"]
        if row["label"]
        in {
            "What is this?",
            "Explain simply",
            "How do I fix this?",
            "Fix grammar",
            "Simplify",
            "Improve tone",
        }
    ]
    assert len(expected) == 6

    for selection_mode in ("key", "click"):
        for caller_idx, glyph, prompt in expected:
            overlay = IntentOverlay(caller_idx=caller_idx)
            chosen: list[tuple[str, str]] = []
            overlay.intent_chosen.connect(lambda key, text: chosen.append((key, text)))
            try:
                overlay.show()
                driver.pump()
                assert overlay.isVisible()
                row_index = next(
                    index
                    for index, row in enumerate(overlay._rows)
                    if row["glyph"] == glyph and row["prompt"] == prompt
                )
                if selection_mode == "key":
                    QTest.keyClick(overlay, getattr(Qt.Key, f"Key_{glyph}"))
                else:
                    assert len(overlay._row_rects) == len(overlay._rows)
                    QTest.mouseClick(
                        overlay,
                        Qt.MouseButton.LeftButton,
                        pos=overlay._row_rects[row_index].center(),
                    )
                driver.wait(lambda: bool(chosen), f"{selection_mode} selection for {glyph}")
                assert chosen == [(glyph, prompt)]
            finally:
                try:
                    overlay.close()
                    overlay.deleteLater()
                except RuntimeError:
                    pass
                driver.pump()
