"""Theme stylesheet contracts for widgets used by app windows."""
from __future__ import annotations

from ui.shared.theme import _app_stylesheet, theme_colors


def test_radio_and_checkbox_indicators_have_explicit_theme_contrast():
    stylesheet = _app_stylesheet(theme_colors(True))

    assert "QRadioButton, QCheckBox, QLabel, QGroupBox" in stylesheet
    assert "QRadioButton::indicator:checked" in stylesheet
    assert "QCheckBox::indicator:checked" in stylesheet
