"""User-workflow tests for the snip overlay region selector.

Drives ui.snip_overlay.SnipOverlay the way a user does — drag a region,
click the capture-mode toolbar, press Escape — and asserts the emitted
mss-style regions and cancel signals. Runs offscreen; mouse/key events are
duck-typed fakes because the handlers only read button()/pos()/key().
"""
from __future__ import annotations

import sys
import time

import pytest

pytest.importorskip("PySide6", reason="PySide6 not installed")

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtWidgets import QApplication

from ui.snip_overlay import SnipOverlay


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication(["wisp-snip-tests"])


class _FakeMouse:
    def __init__(self, pos: QPoint, button=Qt.MouseButton.LeftButton):
        self._pos = pos
        self._button = button

    def button(self):
        return self._button

    def pos(self):
        return self._pos


class _FakeKey:
    def __init__(self, key):
        self._key = key

    def key(self):
        return self._key


def _recorded_signals(overlay: SnipOverlay) -> tuple[list[dict], list[bool]]:
    regions: list[dict] = []
    cancels: list[bool] = []
    overlay.region_selected.connect(regions.append)
    overlay.cancelled.connect(lambda: cancels.append(True))
    return regions, cancels


def _expected_region(overlay: SnipOverlay, rect: QRect) -> dict:
    """Translate a widget-local rect the way the overlay contract promises."""
    left = rect.x() + overlay._virtual_origin.x()
    top = rect.y() + overlay._virtual_origin.y()
    screen = QApplication.screenAt(QPoint(left, top)) or overlay.screen()
    dpr = screen.devicePixelRatio() if screen is not None else 1.0
    return {
        "left": round(left * dpr),
        "top": round(top * dpr),
        "width": round(rect.width() * dpr),
        "height": round(rect.height() * dpr),
    }


def _pump(qapp, seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.005)


def test_normalize_region_accepts_only_usable_dicts(qapp):
    normalize = SnipOverlay._normalize_region
    assert normalize(None) is None
    assert normalize("nope") is None
    assert normalize({"left": 0, "top": 0, "width": 4, "height": 100}) is None
    assert normalize({"left": 0, "top": 0, "width": 100, "height": 3}) is None
    assert normalize({"left": "x", "top": 0, "width": 100, "height": 100}) is None
    assert normalize({"left": 10.6, "top": "20", "width": 99.4, "height": 50}) == {
        "left": 11,
        "top": 20,
        "width": 99,
        "height": 50,
    }


def test_drag_selection_emits_scaled_region_and_closes(qapp):
    overlay = SnipOverlay()
    regions, cancels = _recorded_signals(overlay)

    overlay.mousePressEvent(_FakeMouse(QPoint(10, 10)))
    assert overlay._sel_rect == QRect(QPoint(10, 10), QPoint(10, 10))

    overlay.mouseMoveEvent(_FakeMouse(QPoint(35, 45)))
    assert overlay._sel_rect == QRect(QPoint(10, 10), QPoint(35, 45)).normalized()

    expected = _expected_region(overlay, QRect(QPoint(10, 10), QPoint(60, 80)).normalized())
    overlay.mouseReleaseEvent(_FakeMouse(QPoint(60, 80)))

    assert cancels == []
    assert regions == [expected]
    assert all(isinstance(value, int) for value in regions[0].values())


def test_reverse_drag_normalizes_rect(qapp):
    overlay = SnipOverlay()
    regions, cancels = _recorded_signals(overlay)

    overlay.mousePressEvent(_FakeMouse(QPoint(90, 70)))
    # The overlay normalizes reversed drags with QRect.normalized(), which is
    # what defines the selection contract (including its 1px corner rounding).
    expected = _expected_region(overlay, QRect(QPoint(90, 70), QPoint(20, 30)).normalized())
    overlay.mouseReleaseEvent(_FakeMouse(QPoint(20, 30)))

    assert cancels == []
    assert regions == [expected]
    assert regions[0]["width"] > 4 and regions[0]["height"] > 4


def test_tiny_drag_cancels_instead_of_selecting(qapp):
    overlay = SnipOverlay()
    regions, cancels = _recorded_signals(overlay)

    overlay.mousePressEvent(_FakeMouse(QPoint(10, 10)))
    overlay.mouseReleaseEvent(_FakeMouse(QPoint(12, 12)))

    assert regions == []
    assert cancels == [True]


def test_mouse_move_without_press_is_ignored(qapp):
    overlay = SnipOverlay()
    overlay.mouseMoveEvent(_FakeMouse(QPoint(50, 50)))
    assert overlay._sel_rect is None
    overlay.close()


def test_escape_cancels(qapp):
    overlay = SnipOverlay()
    regions, cancels = _recorded_signals(overlay)

    overlay.keyPressEvent(_FakeKey(Qt.Key.Key_Escape))

    assert regions == []
    assert cancels == [True]


def test_other_keys_do_not_cancel(qapp):
    overlay = SnipOverlay()
    regions, cancels = _recorded_signals(overlay)

    overlay.keyPressEvent(_FakeKey(Qt.Key.Key_A))

    assert regions == []
    assert cancels == []
    overlay.close()


def test_toolbar_offers_app_mode_only_with_valid_region(qapp):
    with_app = SnipOverlay(app_region={"left": 100, "top": 50, "width": 300, "height": 200})
    with_app.grab()
    assert [mode for mode, _rect in with_app._mode_rects] == ["area", "app", "full"]
    with_app.close()

    without_app = SnipOverlay(app_region={"left": 0, "top": 0, "width": 2, "height": 2})
    without_app.grab()
    assert [mode for mode, _rect in without_app._mode_rects] == ["area", "full"]
    without_app.close()


def test_toolbar_hit_testing(qapp):
    overlay = SnipOverlay(app_region={"left": 100, "top": 50, "width": 300, "height": 200})
    overlay.grab()

    for mode, rect in overlay._mode_rects:
        assert overlay._toolbar_mode_at(rect.center()) == mode
    assert overlay._toolbar_mode_at(QPoint(-5, -5)) is None
    # Inside the toolbar frame but in the gap between buttons.
    gap = overlay._toolbar_rect.topLeft() + QPoint(1, 1)
    assert overlay._toolbar_mode_at(gap) is None
    overlay.close()


def test_toolbar_gap_click_does_not_start_drag(qapp):
    overlay = SnipOverlay()
    overlay.grab()

    overlay.mousePressEvent(_FakeMouse(overlay._toolbar_rect.topLeft() + QPoint(1, 1)))

    assert overlay._origin is None
    assert overlay._sel_rect is None
    overlay.close()


def test_toolbar_full_screen_mode_captures_virtual_desktop(qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    overlay = SnipOverlay()
    overlay.grab()
    regions, cancels = _recorded_signals(overlay)
    full_rect = overlay._mode_rects[-1][1]
    expected = _expected_region(overlay, overlay.rect())

    QTest.mouseClick(overlay, Qt.MouseButton.LeftButton, pos=full_rect.center())
    qapp.processEvents()

    assert cancels == []
    assert regions == [expected]


def test_toolbar_app_mode_emits_app_region_copy(qapp):
    app_region = {"left": 100, "top": 50, "width": 300, "height": 200}
    overlay = SnipOverlay(app_region=app_region)
    overlay.grab()
    regions, cancels = _recorded_signals(overlay)
    app_rect = next(rect for mode, rect in overlay._mode_rects if mode == "app")

    overlay.mousePressEvent(_FakeMouse(app_rect.center()))
    qapp.processEvents()

    assert cancels == []
    assert regions == [app_region]
    assert regions[0] is not app_region


def test_full_mode_click_outside_toolbar_selects_everything(qapp):
    overlay = SnipOverlay()
    overlay.grab()
    regions, _cancels = _recorded_signals(overlay)
    overlay._mode = "full"
    expected = _expected_region(overlay, overlay.rect())

    overlay.mousePressEvent(_FakeMouse(QPoint(overlay.width() // 2, overlay.height() // 2)))

    assert regions == [expected]


def test_region_for_darwin_uses_logical_points(qapp, monkeypatch):
    overlay = SnipOverlay()
    origin = overlay._virtual_origin
    monkeypatch.setattr(sys, "platform", "darwin")

    region = overlay._region_for(QRect(5, 7, 50, 60))

    assert region == {
        "left": 5 + origin.x(),
        "top": 7 + origin.y(),
        "width": 50,
        "height": 60,
    }
    overlay.close()


def test_region_translation_uses_live_monitor_origin_and_dpi(qapp, monkeypatch):
    """Monitor/DPI changes are read at selection time instead of cached as pixels."""
    import ui.snip_overlay as snip_module

    class Screen:
        dpr = 1.0

        def devicePixelRatio(self):
            return self.dpr

    screen = Screen()

    class CurrentApplication:
        @staticmethod
        def screenAt(_point):
            return screen

    overlay = SnipOverlay()
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(snip_module, "QApplication", CurrentApplication)
    rect = QRect(5, 7, 50, 60)

    overlay._virtual_origin = QPoint(-100, 20)
    first = overlay._region_for(rect)
    screen.dpr = 2.0
    overlay._virtual_origin = QPoint(40, -30)
    second = overlay._region_for(rect)

    assert first == {"left": -95, "top": 27, "width": 50, "height": 60}
    assert second == {"left": 90, "top": -46, "width": 100, "height": 120}
    overlay.close()


def test_show_focuses_for_capture_and_escape_still_cancels(qapp):
    from PySide6.QtTest import QTest

    overlay = SnipOverlay()
    _regions, cancels = _recorded_signals(overlay)

    overlay.show()
    # Let the 0ms and 75ms deferred focus grabs fire while the overlay lives.
    _pump(qapp, 0.12)
    assert overlay.isVisible()

    QTest.keyClick(overlay, Qt.Key.Key_Escape)
    assert cancels == [True]


def test_focus_for_capture_is_noop_when_hidden_or_unhooked(qapp):
    overlay = SnipOverlay()
    overlay.focus_for_capture()  # not visible: must return without focusing
    overlay._unhook()
    overlay._unhook()  # idempotent
    overlay.focus_for_capture()
    assert overlay._closed is True
    overlay.close()
