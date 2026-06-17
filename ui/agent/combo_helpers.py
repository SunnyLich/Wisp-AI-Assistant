"""Combo-box constants and value helpers for agent task UI."""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox

from ui.i18n import t

I18N_SOURCE_ROLE = 0x0100 + 1
AGENT_ROLE_OPTIONS = (
    "Coordinator",
    "Planner",
    "Implementer",
    "Reviewer",
    "Tester",
    "Researcher",
)
COMMUNICATION_PHASE_OPTIONS = (
    "Planning",
    "Implementation",
    "Review",
    "Testing",
    "Status update",
    "Completion",
)
AGENT_PROVIDER_OPTIONS = (
    "same as task",
    "groq",
    "openai",
    "anthropic",
    "chatgpt",
    "copilot",
)
MAP_AGENT_PROVIDER_OPTIONS = (
    "same as task",
    "copilot",
    "chatgpt",
    "openai",
    "anthropic",
    "groq",
    "google",
)
DEFAULT_AGENT_NAME_OPTIONS = ("Coordinator", "Builder", "Reviewer")
SANDBOX_OPTIONS = (
    "workspace-write: scope folder only",
    "read-only: inspect only",
    "approval-required: ask before every write",
)
PERMISSION_OPTIONS = ("auto", "ask permission", "never permit")
REASONING_OPTIONS = ("low", "medium", "high", "xhigh")
APPROVAL_OPTIONS = (
    "never escalate",
    "auto-approve safe reads",
    "ask before escalation",
)
REPORT_OPTIONS = (
    "Summary + changed files + verification",
    "Patch only",
    "Detailed implementation report",
    "Ask before final changes",
)


def add_translated_combo_items(combo: QComboBox, values: tuple[str, ...]) -> None:
    """Add translated combo items."""
    for value in values:
        combo.addItem(t(value), value)
        combo.setItemData(combo.count() - 1, value, I18N_SOURCE_ROLE)


def combo_value(combo: QComboBox, default: str = "") -> str:
    """Handle combo value for UI agent combo helpers."""
    text = combo.currentText().strip()
    for idx in range(combo.count()):
        if combo.itemText(idx).strip() != text:
            continue
        data = combo.itemData(idx)
        return str(data) if data is not None else (text or default)
    return text or default


def set_combo_value(combo: QComboBox, value: str) -> None:
    """Set combo value."""
    value = str(value or "").strip()
    if not value:
        combo.setCurrentText("")
        return
    for idx in range(combo.count()):
        data = combo.itemData(idx)
        if (
            (data is not None and str(data) == value)
            or combo.itemText(idx) == value
            or combo.itemText(idx) == t(value)
        ):
            combo.setCurrentIndex(idx)
            return
    combo.setCurrentText(value)


def display_known(value: str, options: tuple[str, ...]) -> str:
    """Handle display known for UI agent combo helpers."""
    return t(value) if value in options else value


def display_role(role: str) -> str:
    """Handle display role for UI agent combo helpers."""
    return display_known(role, AGENT_ROLE_OPTIONS)


def display_agent_name(name: str) -> str:
    """Handle display agent name for UI agent combo helpers."""
    return display_known(name, DEFAULT_AGENT_NAME_OPTIONS)


def display_phase(phase: str) -> str:
    """Handle display phase for UI agent combo helpers."""
    return display_known(phase, COMMUNICATION_PHASE_OPTIONS)
