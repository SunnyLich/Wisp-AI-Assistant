from __future__ import annotations

import time

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtTest import QSignalSpy, QTest

from ui.rewrite_annotation import (
    RewriteAnnotationPopup,
    _native_screen_rect_to_qt,
    inline_diff_html,
)


def test_inline_diff_marks_deletions_and_additions() -> None:
    rendered = inline_diff_html("This sentence are long.", "This sentence is short.")

    assert "#ff6b6b" in rendered
    assert "text-decoration:line-through" in rendered
    assert "#51cf66" in rendered
    assert "are" in rendered
    assert "is" in rendered


def test_enter_submits_then_collapses_to_balloon(qapp) -> None:
    popup = RewriteAnnotationPopup(annotation_id="a1", selected_text="old words")
    submitted = QSignalSpy(popup.submitted)
    popup.show_composer()
    popup._comment.setPlainText("Make this clearer")

    QTest.keyClick(popup._comment, Qt.Key.Key_Return)

    assert submitted.count() == 1
    assert list(submitted.at(0)) == ["a1", "Make this clearer", False]
    assert popup.state == "processing"
    assert popup._stack.currentWidget() is popup._balloon
    assert popup._balloon_button.display_number_text == "1"
    popup.remove()


def test_ctrl_enter_forces_document_context(qapp) -> None:
    popup = RewriteAnnotationPopup(annotation_id="a2", selected_text="old words")
    submitted = QSignalSpy(popup.submitted)
    popup.show_composer()
    popup._comment.setPlainText("Match the rest of the document")

    QTest.keyClick(
        popup._comment,
        Qt.Key.Key_Return,
        Qt.KeyboardModifier.ControlModifier,
    )

    assert submitted.count() == 1
    assert list(submitted.at(0)) == ["a2", "Match the rest of the document", True]
    popup.remove()


def test_composer_retries_activation_until_focus_is_stable(qapp, monkeypatch) -> None:
    popup = RewriteAnnotationPopup(annotation_id="focus-guard", selected_text="old words")
    popup.show_composer()
    popup._stop_composer_focus_claim()
    focus_samples = iter((False, True, True, True))
    activations: list[bool] = []

    monkeypatch.setattr(popup, "_composer_has_keyboard_focus", lambda: next(focus_samples))
    monkeypatch.setattr(popup, "_activate_composer", lambda: activations.append(True))
    popup._focus_claim_started = time.monotonic()
    popup._focus_claim_timer.start()

    for _ in range(4):
        popup._focus_claim_tick()

    assert len(activations) == 1
    assert popup._focus_claim_attempts >= 1
    assert not popup._focus_claim_timer.isActive()
    popup.remove()


def test_composer_focus_guard_stops_after_user_switches_apps(qapp, monkeypatch) -> None:
    popup = RewriteAnnotationPopup(annotation_id="focus-switch", selected_text="old words")
    popup.show_composer()
    popup._stop_composer_focus_claim()
    monkeypatch.setattr(popup, "_source_window_state", lambda: (False, None))
    monkeypatch.setattr(
        popup,
        "_activate_composer",
        lambda: pytest.fail("focus guard must not steal focus from another application"),
    )
    popup._focus_claim_started = time.monotonic()
    popup._focus_claim_timer.start()

    popup._focus_claim_tick()

    assert not popup._focus_claim_timer.isActive()
    popup.remove()


def test_processing_balloon_shows_its_comment_number(qapp) -> None:
    popup = RewriteAnnotationPopup(
        annotation_id="numbered",
        display_number=3,
        selected_text="old words",
    )

    popup.show_processing(display_number=7)
    popup.show()
    qapp.processEvents()

    assert popup._balloon_button.display_number_text == "7"
    assert popup._balloon_button.feature_letter_text == "R"
    assert popup._balloon_button._monogram_label.text() == "R7"
    assert popup._balloon_button._monogram_label.geometry() == QRect(6, 5, 36, 33)
    assert "comment 7" in popup._balloon_button.accessibleName()
    assert popup._balloon_button.uses_vector_source
    assert popup.mask().isEmpty()
    popup.remove()


def test_feature_balloon_can_relabel_future_features(qapp) -> None:
    popup = RewriteAnnotationPopup(annotation_id="feature-letter", selected_text="old words")

    popup._balloon_button.set_feature_letter("summarize")

    assert popup._balloon_button.feature_letter_text == "S"
    assert popup._balloon_button._monogram_label.text() == "S1"
    popup.remove()


def test_feature_balloon_keeps_multi_digit_comment_number_inline(qapp) -> None:
    popup = RewriteAnnotationPopup(annotation_id="number-fit", selected_text="old words")

    popup._balloon_button.set_display_number(12)

    assert popup._balloon_button.display_number_text == "12"
    assert popup._balloon_button._monogram_label.text() == "R12"
    assert popup._balloon_button._monogram_label.geometry() == QRect(6, 5, 36, 33)
    popup.remove()


def test_hold_stashes_without_submitting_and_sits_above_send(qapp) -> None:
    popup = RewriteAnnotationPopup(annotation_id="held-1", selected_text="old words")
    held = QSignalSpy(popup.held)
    submitted = QSignalSpy(popup.submitted)
    popup.show_composer()
    popup._comment.setPlainText("Make this clearer later")
    qapp.processEvents()

    assert popup._hold.y() < popup._send.y()
    popup._hold.click()

    assert held.count() == 1
    assert list(held.at(0)) == ["held-1", "Make this clearer later", False]
    assert submitted.count() == 0
    assert popup.state == "held"
    assert not popup.isVisible()
    popup.remove()


def test_close_cancels_only_after_processing_popup_is_reopened(qapp) -> None:
    popup = RewriteAnnotationPopup(annotation_id="a3", selected_text="old words")
    declined = QSignalSpy(popup.declined)
    cancelled = QSignalSpy(popup.cancel_requested)

    popup.show_composer()
    popup._close.click()
    assert declined.count() == 1
    assert cancelled.count() == 0

    popup.show_processing()
    assert popup._stack.currentWidget() is popup._balloon
    popup._balloon_button.click()
    popup._close.click()
    assert cancelled.count() == 1
    popup.remove()


def test_proposal_expands_with_accept_decline_and_revision(qapp) -> None:
    popup = RewriteAnnotationPopup(annotation_id="a4", selected_text="This are wrong.")
    accepted = QSignalSpy(popup.accept_requested)
    revisions = QSignalSpy(popup.revision_requested)

    popup.show_processing()
    popup.show_proposal("This is correct.")

    assert popup.state == "proposal"
    assert popup._stack.currentWidget() is popup._panel
    assert "#ff6b6b" in popup._diff.text()
    assert "#51cf66" in popup._diff.text()
    popup._accept.click()
    assert list(accepted.at(0)) == ["a4", "This is correct."]
    assert not popup.isVisible()

    # An application failure explicitly restores the same proposal so the user
    # can retry or copy it instead of leaving a dead hidden annotation.
    popup.show_proposal("This is correct.")
    popup._revision.setPlainText("Make it friendlier")
    QTest.keyClick(popup._revision, Qt.Key.Key_Return)
    assert list(revisions.at(0)) == ["a4", "Make it friendlier"]
    assert popup.state == "processing"
    popup.remove()


def test_copy_only_proposal_relabels_accept(qapp) -> None:
    popup = RewriteAnnotationPopup(annotation_id="a5", selected_text="old")
    popup.show_proposal("new", copy_only=True)

    assert popup._accept.text() == "Copy"
    popup.remove()


def test_comment_editor_and_popup_expand_with_content(qapp) -> None:
    popup = RewriteAnnotationPopup(annotation_id="a6", selected_text="old")
    popup.show_composer()
    qapp.processEvents()
    initial_editor_height = popup._comment.height()
    initial_popup_height = popup.height()

    popup._comment.setPlainText("\n".join(f"Detailed instruction {index}" for index in range(10)))
    qapp.processEvents()

    assert popup._comment.height() > initial_editor_height
    assert popup.height() > initial_popup_height
    assert popup._comment.height() <= 220
    assert "Enter: Send" in popup._hint.text()
    assert "Ctrl+Enter: Include document" in popup._hint.text()
    assert "Shift+Enter: New line" in popup._hint.text()
    popup.remove()


def test_composer_controls_are_inside_the_native_popup_window(qapp) -> None:
    popup = RewriteAnnotationPopup(annotation_id="contained", selected_text="old")
    popup.show_composer()
    qapp.processEvents()

    for widget in (popup._close, popup._comment, popup._hint, popup._hold, popup._send):
        top_left = widget.mapTo(popup, QPoint(0, 0))
        assert top_left.x() >= 0
        assert top_left.y() >= 0
        assert top_left.x() + widget.width() <= popup.width()
        assert top_left.y() + widget.height() <= popup.height()
    popup.remove()


def test_proposal_restores_popup_height_after_balloon(qapp) -> None:
    popup = RewriteAnnotationPopup(annotation_id="a7", selected_text="old words")
    popup.show_processing()
    assert popup.height() == 44

    popup.show_proposal("new words")
    qapp.processEvents()

    assert popup.height() > 44
    assert popup.width() == 390
    popup.remove()


def test_popup_anchors_beside_captured_selection(qapp) -> None:
    popup = RewriteAnnotationPopup(
        annotation_id="a8",
        selected_text="selected words",
        selection_rect={"left": 100, "top": 120, "width": 80, "height": 20},
    )

    popup.show_composer()
    qapp.processEvents()

    assert popup.x() == 190
    assert popup.y() == 108
    popup.remove()


def test_processing_balloon_keeps_the_composer_anchor_side(qapp) -> None:
    available = qapp.primaryScreen().availableGeometry()
    selection = QRect(available.right() - 90, available.top() + 160, 70, 20)
    popup = RewriteAnnotationPopup(
        annotation_id="edge-anchor",
        selected_text="selected words",
        selection_rect={
            "left": selection.left(),
            "top": selection.top(),
            "width": selection.width(),
            "height": selection.height(),
        },
    )

    popup.show_composer()
    qapp.processEvents()
    assert popup.geometry().right() < selection.left()

    popup.show_processing()
    qapp.processEvents()
    endpoint = QPoint(selection.x() + selection.width(), selection.center().y())
    assert popup.pos() + popup._balloon_button.tail_tip == endpoint
    popup.remove()


def test_processing_balloon_points_back_to_selection_on_right(qapp) -> None:
    selection = QRect(100, 160, 70, 20)
    popup = RewriteAnnotationPopup(
        annotation_id="right-anchor",
        selected_text="selected words",
        selection_rect={
            "left": selection.left(),
            "top": selection.top(),
            "width": selection.width(),
            "height": selection.height(),
        },
    )

    popup.show_composer()
    popup.show_processing()
    qapp.processEvents()

    assert popup._selection_anchor_side == "right"
    assert popup._balloon_button.tail_direction == "left"
    endpoint = QPoint(selection.x() + selection.width(), selection.center().y())
    assert popup.pos() + popup._balloon_button.tail_tip == endpoint
    popup.remove()


def test_processing_tail_uses_explicit_native_character_endpoint(qapp) -> None:
    popup = RewriteAnnotationPopup(
        annotation_id="native-endpoint",
        selected_text="selected words",
        selection_rect={
            "left": 300,
            "top": 160,
            "width": 2,
            "height": 20,
            "endpoint_x": 300,
            "endpoint_y": 170,
        },
    )

    popup.show_composer()
    popup.show_processing()
    qapp.processEvents()

    assert popup.pos() + popup._balloon_button.tail_tip == QPoint(300, 170)
    popup.remove()


def test_native_anchor_converts_inside_negative_origin_mixed_dpi_monitor(monkeypatch) -> None:
    """Native physical pixels map to Qt DIPs relative to the owning monitor."""
    import ui.rewrite_annotation as rewrite_annotation

    class FakeScreen:
        def __init__(self, geometry: QRect, dpr: float) -> None:
            self._geometry = geometry
            self._dpr = dpr

        def geometry(self) -> QRect:
            return QRect(self._geometry)

        def devicePixelRatio(self) -> float:
            return self._dpr

    monkeypatch.setattr(rewrite_annotation.sys, "platform", "win32")
    converted = _native_screen_rect_to_qt(
        {
            "left": -2260.0,
            "top": 150.0,
            "width": 90.0,
            "height": 30.0,
            "endpoint_x": -2170.0,
            "endpoint_y": 165.0,
        },
        screens=[
            FakeScreen(QRect(0, 0, 1920, 1080), 1.0),
            FakeScreen(QRect(-2560, 0, 1707, 960), 1.5),
        ],
    )

    assert converted == {
        "left": -2360.0,
        "top": 100.0,
        "width": 60.0,
        "height": 20.0,
        "endpoint_x": -2300.0,
        "endpoint_y": 110.0,
    }


def test_absolute_anchor_refresh_does_not_double_apply_window_move(qapp, monkeypatch) -> None:
    popup = RewriteAnnotationPopup(
        annotation_id="absolute-refresh",
        selected_text="selected words",
        source_window_id=101,
        selection_rect={"left": 100, "top": 120, "width": 80, "height": 20},
    )
    first_window = QRect(20, 30, 800, 600)
    moved_window = QRect(70, 75, 800, 600)
    assert popup._selection_anchor_for_source(first_window) == QRect(100, 120, 80, 20)
    assert popup._selection_anchor_for_source(moved_window) == QRect(150, 165, 80, 20)
    monkeypatch.setattr(popup, "_source_window_state", lambda: (True, moved_window))

    popup.update_selection_anchor(
        {"left": 150, "top": 165, "width": 80, "height": 20},
        visible=True,
    )

    assert popup._selection_anchor_for_source(moved_window) == QRect(150, 165, 80, 20)
    popup.remove()


def test_processing_balloon_remains_visible_at_every_screen_corner(qapp) -> None:
    available = qapp.primaryScreen().availableGeometry()
    endpoints = (
        available.topLeft(),
        available.topRight(),
        available.bottomLeft(),
        available.bottomRight(),
    )
    for index, endpoint in enumerate(endpoints):
        popup = RewriteAnnotationPopup(
            annotation_id=f"corner-{index}",
            selected_text="selected words",
            selection_rect={
                "left": endpoint.x(),
                "top": endpoint.y(),
                "width": 1,
                "height": 1,
                "endpoint_x": endpoint.x(),
                "endpoint_y": endpoint.y(),
            },
        )
        popup.show_processing()
        qapp.processEvents()

        assert available.contains(popup.geometry())
        actual_tip = popup.pos() + popup._balloon_button.tail_tip
        delta = actual_tip - endpoint
        assert delta.x() * delta.x() + delta.y() * delta.y() <= 22 * 22 + 1
        popup.remove()


def test_accept_and_decline_hide_proposals_immediately(qapp) -> None:
    accepted_popup = RewriteAnnotationPopup(annotation_id="accept-now", selected_text="old")
    accepted = QSignalSpy(accepted_popup.accept_requested)
    accepted_popup.show_proposal("two words")
    qapp.processEvents()
    accepted_popup._accept.click()

    assert accepted.count() == 1
    assert not accepted_popup.isVisible()

    declined_popup = RewriteAnnotationPopup(annotation_id="decline-now", selected_text="old")
    declined = QSignalSpy(declined_popup.declined)
    declined_popup.show_proposal("two words")
    qapp.processEvents()
    declined_popup._decline.click()

    assert declined.count() == 1
    assert not declined_popup.isVisible()
    accepted_popup.remove()
    declined_popup.remove()


def test_rewrite_text_and_popup_visibility_stay_in_lockstep(qapp) -> None:
    """Every rewrite state must show or hide its text and top-level popup together."""
    popup = RewriteAnnotationPopup(
        annotation_id="visibility-lifecycle",
        selected_text="This are rough.",
    )
    accepted = QSignalSpy(popup.accept_requested)
    declined = QSignalSpy(popup.declined)

    try:
        assert popup.isHidden()

        popup.show_composer()
        qapp.processEvents()
        assert popup.isVisible()
        assert popup._comment.isVisible()
        assert popup._stack.currentWidget() is popup._panel
        assert popup._diff.isHidden()

        popup._comment.setPlainText("Make this clearer")
        popup._send.click()
        qapp.processEvents()
        assert popup.isVisible()
        assert popup.state == "processing"
        assert popup._stack.currentWidget() is popup._balloon
        assert popup._balloon_button.isVisible()
        assert not popup._comment.isVisible()

        popup.show_proposal("This is clear.")
        qapp.processEvents()
        assert popup.isVisible()
        assert popup._stack.currentWidget() is popup._panel
        assert popup._diff.isVisible()
        assert popup.replacement_text == "This is clear."
        assert "clear" in popup._diff.text()
        assert not popup._balloon_button.isVisible()

        popup._accept.click()
        qapp.processEvents()
        assert accepted.count() == 1
        assert popup.isHidden()
        assert not popup._diff.isVisible()

        # A failed native apply can restore the same proposal.  The containing
        # popup must return with it instead of leaving detached/stale text.
        popup.show_proposal("This is clear.")
        qapp.processEvents()
        assert popup.isVisible()
        assert popup._diff.isVisible()

        popup._decline.click()
        qapp.processEvents()
        assert declined.count() == 1
        assert popup.isHidden()
        assert not popup._diff.isVisible()

        # Reusing the annotation for another comment must not reveal the old
        # proposal while the composer reappears.
        popup.show_composer()
        qapp.processEvents()
        assert popup.isVisible()
        assert popup._comment.isVisible()
        assert popup._diff.isHidden()

        popup.remove()
        qapp.processEvents()
        assert popup.isHidden()
    finally:
        try:
            popup.remove()
        except RuntimeError:
            pass


def test_selection_anchor_follows_source_window_movement(qapp) -> None:
    popup = RewriteAnnotationPopup(
        annotation_id="a9",
        selected_text="selected words",
        selection_rect={"left": 100, "top": 120, "width": 80, "height": 20},
    )

    first = popup._selection_anchor_for_source(QRect(20, 30, 800, 600))
    moved = popup._selection_anchor_for_source(QRect(70, 75, 800, 600))

    assert first == QRect(100, 120, 80, 20)
    assert moved == QRect(150, 165, 80, 20)
    popup.remove()


def test_selection_anchor_follows_scroll_and_hides_when_offscreen(qapp) -> None:
    popup = RewriteAnnotationPopup(
        annotation_id="scroll-anchor",
        selected_text="selected words",
        selection_rect={"left": 100, "top": 120, "width": 80, "height": 20},
    )
    popup.show_proposal("replacement words")
    qapp.processEvents()
    first = popup.pos()
    assert popup.isVisible()
    assert popup._diff.isVisible()
    assert "replacement" in popup._diff.text()

    popup.update_selection_anchor(
        {"left": 100, "top": 200, "width": 80, "height": 20},
        visible=True,
    )
    qapp.processEvents()
    assert popup.y() == first.y() + 80
    assert popup.isVisible()
    assert popup._diff.isVisible()

    popup.update_selection_anchor(None, visible=False)
    qapp.processEvents()
    assert not popup.isVisible()
    assert not popup._diff.isVisible()

    popup.update_selection_anchor(
        {"left": 100, "top": 140, "width": 80, "height": 20},
        visible=True,
    )
    qapp.processEvents()
    assert popup.isVisible()
    assert popup._diff.isVisible()
    assert "replacement" in popup._diff.text()
    assert popup.y() == 128
    popup.remove()


def test_processing_balloon_follows_scroll_hides_and_reappears(qapp) -> None:
    popup = RewriteAnnotationPopup(
        annotation_id="scroll-balloon",
        display_number=4,
        selected_text="selected words",
        selection_rect={"left": 180, "top": 260, "width": 90, "height": 20},
    )
    popup.show_composer()
    popup.show_processing()
    qapp.processEvents()
    first = popup.pos()

    popup.update_selection_anchor(
        {"left": 180, "top": 180, "width": 90, "height": 20},
        visible=True,
    )
    qapp.processEvents()
    assert popup.state == "processing"
    assert popup._stack.currentWidget() is popup._balloon
    assert popup._balloon_button.display_number_text == "4"
    assert popup.y() == first.y() - 80

    popup.update_selection_anchor(None, visible=False)
    qapp.processEvents()
    assert not popup.isVisible()
    assert not popup._balloon_button.isVisible()

    popup.update_selection_anchor(
        {"left": 180, "top": 220, "width": 90, "height": 20},
        visible=True,
    )
    qapp.processEvents()
    assert popup.isVisible()
    assert popup._balloon_button.isVisible()
    assert popup.state == "processing"
    assert popup._stack.currentWidget() is popup._balloon
    assert popup.y() == first.y() - 40
    popup.remove()


def test_popup_and_text_hide_when_source_loses_focus_then_reappear_together(qapp, monkeypatch) -> None:
    """Clicking another window hides the whole annotation until its source is active again."""
    popup = RewriteAnnotationPopup(
        annotation_id="source-focus-lifecycle",
        selected_text="selected words",
        source_window_id=101,
        selection_rect={"left": 220, "top": 240, "width": 90, "height": 20},
    )
    states = iter(
        (
            (True, QRect(100, 100, 900, 700)),
            (False, QRect(100, 100, 900, 700)),
            (True, QRect(100, 100, 900, 700)),
        )
    )
    monkeypatch.setattr(popup, "_source_window_state", lambda: next(states))

    popup.show_proposal("replacement words")
    qapp.processEvents()
    assert popup.isVisible()
    assert popup._diff.isVisible()

    popup._sync_to_source_window()
    qapp.processEvents()
    assert not popup.isVisible()
    assert not popup._diff.isVisible()

    popup._sync_to_source_window()
    qapp.processEvents()
    assert popup.isVisible()
    assert popup._diff.isVisible()
    assert "replacement" in popup._diff.text()
    popup.remove()


def test_processing_balloon_survives_temporary_source_focus_loss(qapp, monkeypatch) -> None:
    """A screen-snipping tool taking focus must not make active work disappear."""
    popup = RewriteAnnotationPopup(
        annotation_id="snip-focus",
        selected_text="selected words",
        source_window_id=101,
        selection_rect={"left": 220, "top": 240, "width": 90, "height": 20},
    )
    source_rect = QRect(100, 100, 900, 700)
    monkeypatch.setattr(
        popup,
        "_source_window_state",
        lambda: (False, source_rect),
    )

    popup.show_processing()
    qapp.processEvents()
    popup._sync_to_source_window()
    qapp.processEvents()

    assert popup.isVisible()
    assert popup._balloon_button.isVisible()
    popup.remove()
