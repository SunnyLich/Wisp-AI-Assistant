"""First-run profile setup wizard for Wisp.

The wizard deliberately asks only the choices that make the app usable on day
one.  Everything it records can later be changed in Settings.
"""
from __future__ import annotations

import sys
from collections.abc import Callable
from urllib.parse import urlparse

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core import secret_store
from ui.i18n import LANGUAGE_OPTIONS, localize_widget_tree, t
from ui.settings_panel import env as settings_env
from ui.shared.window_utils import enable_standard_window_controls, fit_window_to_screen

_PROVIDER_DEFAULTS: dict[str, str] = {
    "groq": "llama-3.3-70b-versatile",
    "openai": "gpt-5.5",
    "anthropic": "claude-sonnet-4-6",
    "google": "gemini-3.5-flash",
    "deepseek": "deepseek-chat",
    "openrouter": "openai/gpt-5.5",
    "mistral": "mistral-large-latest",
    "xai": "grok-4.3",
    "together": "meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8",
    "cerebras": "llama-4-scout-17b-16e-instruct",
    "zai": "glm-4.5-flash",
    "nvidia": "meta/llama-3.3-70b-instruct",
    "sambanova": "Meta-Llama-3.1-8B-Instruct",
    "github_models": "openai/gpt-5.4-mini",
    "huggingface": "meta-llama/Llama-3.3-70B-Instruct",
    "chutes": "deepseek-ai/DeepSeek-V3-0324",
    "vercel": "openai/gpt-5.4-mini",
    "fireworks": "accounts/fireworks/models/llama-v3p1-8b-instruct",
    "cohere": "command-r-plus",
    "ai21": "jamba-large",
    "nebius": "meta-llama/Meta-Llama-3.3-70B-Instruct",
    "ollama": "llama3.3",
    "custom": "",
}

_PROVIDER_SECRET_NAMES: dict[str, str] = {
    "groq": "GROQ_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "google": "GOOGLE_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "xai": "XAI_API_KEY",
    "together": "TOGETHER_API_KEY",
    "cerebras": "CEREBRAS_API_KEY",
    "zai": "ZAI_API_KEY",
    "nvidia": "NVIDIA_API_KEY",
    "sambanova": "SAMBANOVA_API_KEY",
    "github_models": "GITHUB_MODELS_API_KEY",
    "huggingface": "HUGGINGFACE_API_KEY",
    "chutes": "CHUTES_API_KEY",
    "vercel": "VERCEL_API_KEY",
    "fireworks": "FIREWORKS_API_KEY",
    "cohere": "COHERE_API_KEY",
    "ai21": "AI21_API_KEY",
    "nebius": "NEBIUS_API_KEY",
    "custom": "CUSTOM_API_KEY",
}

_BUILTIN_PROFILE_IDS = {"default", "fast", "balanced", "deep", "private", "coding"}
_PERSONAL_PROFILE_DEFAULTS = {
    "LLM_PROVIDER": "openai",
    "LLM_MODEL": "gpt-5.5",
    "LLM_FALLBACKS": "",
    "VISION_LLM_PROVIDER": "",
    "VISION_LLM_MODEL": "",
    "VISION_LLM_FALLBACKS": "",
    "CONTEXT_DOCUMENTS_MODE": "off",
    "CONTEXT_BROWSER_MODE": "off",
    "CONTEXT_GITHUB_MODE": "off",
    "CONTEXT_MEMORY_MODE": "off",
    "CONTEXT_SCREENSHOT": "off",
    "FILE_ACCESS": "off",
    "CONTEXT_BROWSER_MAX_CHARS": "12000",
    "CONTEXT_AMBIENT_DOCUMENT_MAX_CHARS": "8000",
    "CONTEXT_TOOL_DOCUMENT_MAX_CHARS": "50000",
    "TOOL_TURN_MAX_CALLS": "25",
    "TOOL_TURN_MAX_RESULT_CHARS": "120000",
    "TOOL_TURN_MAX_TOTAL_CHARS": "300000",
}
_PROFILE_SCOPED_SETUP_KEYS = frozenset(_PERSONAL_PROFILE_DEFAULTS) | {
    "CHAT_LLM_PROVIDER",
    "CHAT_LLM_MODEL",
    "CHAT_LLM_FALLBACKS",
    "MEMORY_LLM_PROVIDER",
    "MEMORY_LLM_MODEL",
    "MEMORY_LLM_FALLBACKS",
}


def clean_profile_name(value: str) -> str:
    """Return a compact, safe profile display name."""
    return " ".join(str(value or "").split())[:80]


def _profile_id(value: str, default: str = "personal") -> str:
    """Return the config-safe id used by a named custom profile."""
    import re

    text = re.sub(r"[^a-z0-9_-]+", "-", clean_profile_name(value).lower()).strip("-")
    return text or default


def personal_profile_values(
    *,
    name: str,
    setup_values: dict[str, str],
    existing_env: dict[str, str],
) -> dict[str, str]:
    """Create a new wizard-owned custom profile without replacing another setup."""
    label = clean_profile_name(name)
    if not label:
        return {}
    try:
        count = max(0, int(str(existing_env.get("PROFILE_COUNT", "0") or "0")))
    except ValueError:
        count = 0

    profiles = [
        (
            slot,
            str(existing_env.get(f"PROFILE_{slot}_ID", "") or "").strip(),
            clean_profile_name(existing_env.get(f"PROFILE_{slot}_LABEL", "")),
        )
        for slot in range(1, count + 1)
        if str(existing_env.get(f"PROFILE_{slot}_ID", "") or "").strip()
    ]
    desired_id = _profile_id(label)
    active_id = str(
        existing_env.get("SETTINGS_PROFILE")
        or existing_env.get("ACTIVE_PROFILE")
        or ""
    ).strip()

    existing_ids = _BUILTIN_PROFILE_IDS | {
        profile_id for _slot, profile_id, _label in profiles
    }
    profile_id = desired_id
    suffix = 2
    while profile_id in existing_ids:
        profile_id = f"{desired_id}-{suffix}"
        suffix += 1
    slot = count + 1
    count = slot

    # The wizard only exposes a subset of profile settings. Seed everything
    # else from the currently selected custom profile (or the legacy top-level
    # setup for built-in profiles), then overlay the choices made in the wizard.
    active_profile = next(
        (profile for profile in profiles if profile[1] == active_id),
        None,
    )
    source_prefix = f"PROFILE_{active_profile[0]}_" if active_profile else ""

    def _source_value(key: str) -> str | None:
        if source_prefix and f"{source_prefix}{key}" in existing_env:
            return str(existing_env[f"{source_prefix}{key}"])
        if key in existing_env:
            return str(existing_env[key])
        return None

    profile = dict(_PERSONAL_PROFILE_DEFAULTS)
    for key in profile:
        if (source_value := _source_value(key)) is not None:
            profile[key] = source_value
    for key in profile:
        if key in setup_values:
            profile[key] = str(setup_values[key])

    memory_provider = _source_value("MEMORY_LLM_PROVIDER")
    memory_model = _source_value("MEMORY_LLM_MODEL")
    memory_fallbacks = _source_value("MEMORY_LLM_FALLBACKS")
    profile["MEMORY_LLM_PROVIDER"] = str(memory_provider or profile["LLM_PROVIDER"])
    profile["MEMORY_LLM_MODEL"] = str(memory_model or profile["LLM_MODEL"])
    profile["MEMORY_LLM_FALLBACKS"] = str(memory_fallbacks or "")

    values = {
        "PROFILE_COUNT": str(count),
        f"PROFILE_{slot}_ID": profile_id,
        f"PROFILE_{slot}_LABEL": label,
        "ACTIVE_PROFILE": profile_id,
        "SETTINGS_PROFILE": profile_id,
    }
    values.update({f"PROFILE_{slot}_{key}": value for key, value in profile.items()})
    return values


def persisted_profile_setup_values(
    *,
    name: str,
    setup_values: dict[str, str],
    existing_env: dict[str, str],
) -> dict[str, str]:
    """Return non-destructive wizard writes plus one new named profile."""
    values = {
        key: value
        for key, value in setup_values.items()
        if key not in _PROFILE_SCOPED_SETUP_KEYS
    }
    values.update(
        personal_profile_values(
            name=name,
            setup_values=setup_values,
            existing_env=existing_env,
        )
    )
    return values


def should_show_onboarding(env: dict[str, str], *, env_file_exists: bool) -> bool:
    """Show automatically only for a genuinely fresh installation.

    Existing installations may have a .env without the new marker, so treating
    any pre-existing file as configured avoids interrupting established users.
    """
    value = str(env.get("WISP_ONBOARDING_COMPLETE") or "").strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return False
    if value in {"0", "false", "no", "off"}:
        return True
    return not env_file_exists


def profile_values(
    *,
    name: str,
    setup_mode: str,
    provider: str = "",
    model: str = "",
    custom_base_url: str = "",
    oauth_connected: bool = False,
    tts_preference: str = "none",
    stt_preference: str = "none",
    app_language: str = "",
    assistant_language: str = "",
    theme_mode: str = "system",
) -> dict[str, str]:
    """Translate wizard answers into non-secret .env values."""
    mode = "advanced" if setup_mode == "advanced" else "simple"
    values = {
        "WISP_ONBOARDING_COMPLETE": "True",
        "WISP_PROFILE_NAME": clean_profile_name(name),
        "WISP_SETUP_MODE": mode,
        "WISP_TTS_PREFERENCE": tts_preference,
        "WISP_STT_PREFERENCE": stt_preference,
        "APP_LANGUAGE": app_language,
        "ASSISTANT_LANGUAGE": assistant_language,
        "THEME_MODE": theme_mode if theme_mode in {"system", "light", "dark"} else "system",
    }
    selected_provider = provider if mode == "advanced" else ""
    if selected_provider in _PROVIDER_DEFAULTS:
        values.update(
            {
                "LLM_PROVIDER": selected_provider,
                "LLM_MODEL": model.strip() or _PROVIDER_DEFAULTS[selected_provider],
            }
        )
        if selected_provider == "custom":
            values["CUSTOM_BASE_URL"] = custom_base_url.strip()
    elif oauth_connected:
        values.update({"LLM_PROVIDER": "chatgpt", "LLM_MODEL": "gpt-5.5"})

    # Local options are immediately useful and supported by the app. Cloud
    # options are kept as an explicit preference until the user configures the
    # required provider credentials in Settings.
    if tts_preference == "local":
        values.update({"TTS_PROVIDER": "kokoro", "TTS_SPEAK_REPLIES": "True"})
    if stt_preference == "local":
        values.update({"STT_MODEL": "base", "STT_DEVICE": "auto"})
    return values


def local_speech_install_request(
    *,
    tts_preference: str,
    stt_preference: str,
    settings: dict[str, str],
) -> dict[str, object] | None:
    """Build one staged installer request for the wizard's local speech choices."""
    install_tts = str(tts_preference).strip().lower() == "local"
    install_stt = str(stt_preference).strip().lower() == "local"
    if not install_tts and not install_stt:
        return None

    from core import optional_deps

    def unique(items: list[str]) -> list[str]:
        return list(dict.fromkeys(items))

    packages: list[str] = []
    pre_install_packages: list[str] = []
    remove_artifacts: list[str] = []
    settings_updates: dict[str, str] = {}
    extra: dict[str, object] = {}
    if install_tts:
        kokoro_device = str(settings.get("KOKORO_DEVICE") or "auto")
        pre_install_packages.extend(optional_deps.kokoro_torch_install_packages(kokoro_device))
        packages.extend(optional_deps.kokoro_install_packages(kokoro_device))
        remove_artifacts.extend(optional_deps.kokoro_remove_artifacts())
        settings_updates.update(
            {
                "TTS_PROVIDER": "kokoro",
                "WISP_TTS_PREFERENCE": "local",
                "KOKORO_VOICE": str(settings.get("KOKORO_VOICE") or "af_heart"),
                "KOKORO_LANG_CODE": str(settings.get("KOKORO_LANG_CODE") or "a"),
                "KOKORO_DEVICE": kokoro_device,
            }
        )
        extra.update(
            {
                "kokoro_voice": settings_updates["KOKORO_VOICE"],
                "kokoro_require_gpu": False,
                "kokoro_install_device": (
                    "cuda"
                    if optional_deps.kokoro_install_mode_for_device(kokoro_device) == "gpu"
                    else "cpu"
                ),
            }
        )
    if install_stt:
        stt_device = str(settings.get("STT_DEVICE") or "auto")
        packages.extend(optional_deps.stt_install_packages(stt_device))
        remove_artifacts.extend(optional_deps.stt_remove_artifacts())
        settings_updates.update(
            {
                "WISP_STT_PREFERENCE": "local",
                "STT_MODEL": str(settings.get("STT_MODEL") or "base"),
                "STT_DEVICE": stt_device,
                "STT_COMPUTE_TYPE": str(settings.get("STT_COMPUTE_TYPE") or "int8"),
                "STT_LANGUAGE": str(settings.get("STT_LANGUAGE") or "en"),
                "STT_BEAM_SIZE": str(settings.get("STT_BEAM_SIZE") or "5"),
            }
        )
        extra.update(
            {
                "stt_model": settings_updates["STT_MODEL"],
                "stt_device": stt_device,
                "stt_compute_type": settings_updates["STT_COMPUTE_TYPE"],
            }
        )

    if install_tts and install_stt:
        display_name = "Local speech"
        post_install = "speech_prepare"
    elif install_tts:
        display_name = "Kokoro"
        post_install = "kokoro_prepare"
    else:
        display_name = "STT"
        post_install = "stt_prepare"
    return {
        "display_name": display_name,
        "packages": unique(packages),
        "pre_install_packages": unique(pre_install_packages),
        "remove_artifacts": unique(remove_artifacts),
        "external_plan_extra": {
            **extra,
            "post_install": post_install,
            "settings_updates": settings_updates,
        },
    }


_ONBOARDING_SPEECH_INSTALL_DIALOGS: list[object] = []


def launch_local_speech_installer(request: dict[str, object]) -> object:
    """Open the real staged speech installer for a completed setup wizard."""
    from ui.optional_install_dialog import OptionalInstallDialog
    from ui.settings_panel.dialog import _optional_install_env, _optional_install_plan_command

    display_name = str(request.get("display_name") or "Local speech")
    command, root, log_path, status_path = _optional_install_plan_command(
        display_name=display_name,
        packages=list(request.get("packages") or []),
        pre_install_packages=list(request.get("pre_install_packages") or []),
        remove_artifacts=list(request.get("remove_artifacts") or []),
        external_plan_extra=dict(request.get("external_plan_extra") or {}),
    )
    dialog = OptionalInstallDialog(
        title=t("Wisp {display_name} installer").format(display_name=display_name),
        subtitle=t("Downloading and preparing the local speech features selected during setup."),
        command=command,
        cwd=root,
        log_path=log_path,
        status_path=status_path,
        env=_optional_install_env(),
        mirror_output_to_log=False,
        parent=None,
        auto_start=True,
    )
    dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
    _ONBOARDING_SPEECH_INSTALL_DIALOGS.append(dialog)

    def forget(_obj=None, *, target=dialog) -> None:
        if target in _ONBOARDING_SPEECH_INSTALL_DIALOGS:
            _ONBOARDING_SPEECH_INSTALL_DIALOGS.remove(target)

    dialog.destroyed.connect(forget)
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    return dialog


class OnboardingWizard(QDialog):
    """A small, non-blocking step-by-step first-run setup dialog."""

    def __init__(self, parent=None, *, on_complete: Callable[[bool], None] | None = None) -> None:
        super().__init__(parent)
        self._on_complete = on_complete
        self._oauth_connected = False
        self._oauth_in_progress = False
        self._oauth_error = ""
        self.open_chat_requested = False
        import config

        self._previous_app_language = str(getattr(config, "APP_LANGUAGE", "") or "")
        self._previous_theme_mode = str(getattr(config, "THEME_MODE", "system") or "system")
        self.setWindowTitle("Welcome to Wisp")
        self.setModal(False)
        self.setMinimumWidth(540)
        self.resize(620, 460)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 22)
        layout.setSpacing(14)
        self._progress = QLabel()
        self._progress.setStyleSheet("color: palette(placeholder-text);")
        layout.addWidget(self._progress)
        self._pages = QStackedWidget()
        layout.addWidget(self._pages, 1)

        buttons = QHBoxLayout()
        self._back = QPushButton("Back")
        self._next = QPushButton("Continue")
        # Enter in a text field must advance the wizard. Without this, Qt picks
        # the first auto-default button (Back) and returns the user to setup mode.
        self._back.setAutoDefault(False)
        self._back.setDefault(False)
        self._next.setDefault(True)
        self._back.clicked.connect(self._go_back)
        self._next.clicked.connect(self._go_next)
        buttons.addWidget(self._back)
        buttons.addStretch()
        buttons.addWidget(self._next)
        layout.addLayout(buttons)

        self._build_pages()
        enable_standard_window_controls(self)
        fit_window_to_screen(self, preferred_width=620, preferred_height=460)
        self._show_page(0)
        localize_widget_tree(self)
        self._refresh_trial_steps()

    @staticmethod
    def _page(title: str, body: str) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 6, 4, 4)
        layout.setSpacing(12)
        heading = QLabel(title)
        heading.setStyleSheet("font-size: 20px; font-weight: 650;")
        layout.addWidget(heading)
        description = QLabel(body)
        description.setWordWrap(True)
        description.setStyleSheet("color: palette(placeholder-text);")
        layout.addWidget(description)
        return page, layout

    def _build_pages(self) -> None:
        page, layout = self._page(
            "Choose a language",
            "Choose Wisp’s interface language and the language you want the assistant to use in its replies. You can change both later in Settings.",
        )
        app_language_label = QLabel("Wisp interface language")
        self._app_language = QComboBox()
        for label, value in LANGUAGE_OPTIONS:
            self._app_language.addItem(label, value)
        self._app_language.currentIndexChanged.connect(self._preview_app_language)
        assistant_language_label = QLabel("Assistant response language")
        self._assistant_language = QComboBox()
        for label, value in (
            ("System default", ""),
            ("Match user language", "match_user"),
            ("English", "English"),
            ("Chinese", "Chinese"),
            ("Chinese (Traditional)", "Chinese (Traditional)"),
            ("Spanish", "Spanish"),
            ("French", "French"),
            ("German", "German"),
            ("Japanese", "Japanese"),
            ("Korean", "Korean"),
            ("Portuguese", "Portuguese"),
            ("Hindi", "Hindi"),
        ):
            self._assistant_language.addItem(label, value)
        layout.addWidget(app_language_label)
        layout.addWidget(self._app_language)
        layout.addWidget(assistant_language_label)
        layout.addWidget(self._assistant_language)
        theme_label = QLabel("Theme")
        self._theme_mode = QComboBox()
        self._theme_mode.addItem("System default", "system")
        self._theme_mode.addItem("Light", "light")
        self._theme_mode.addItem("Dark", "dark")
        current_theme = self._theme_mode.findData(self._previous_theme_mode)
        self._theme_mode.setCurrentIndex(current_theme if current_theme >= 0 else 0)
        self._theme_mode.currentIndexChanged.connect(self._preview_theme)
        layout.addWidget(theme_label)
        layout.addWidget(self._theme_mode)
        layout.addStretch()
        self._pages.addWidget(page)

        page, layout = self._page(
            "Let’s set up Wisp",
            "You can change every choice later in Settings. Start simple, or choose advanced if you already know your preferred AI provider.",
        )
        self._mode_group = QButtonGroup(self)
        self._simple_mode = QRadioButton("Simple setup — get a working assistant quickly")
        self._advanced_mode = QRadioButton("Advanced setup — choose provider and optional API key")
        self._simple_mode.setChecked(True)
        self._mode_group.addButton(self._simple_mode)
        self._mode_group.addButton(self._advanced_mode)
        layout.addWidget(self._simple_mode)
        layout.addWidget(self._advanced_mode)
        layout.addStretch()
        self._pages.addWidget(page)

        page, layout = self._page(
            "What should Wisp call you?",
            "This creates your local profile. Your name stays on this device and helps Wisp make conversations feel a little more natural.",
        )
        self._name = QLineEdit()
        self._name.setPlaceholderText("Your name")
        self._name.setMaxLength(80)
        self._name.textChanged.connect(lambda _text: self._update_navigation())
        layout.addWidget(self._name)
        layout.addStretch()
        self._pages.addWidget(page)

        page, layout = self._page(
            "Choose your provider",
            "Optional. Choose any provider Wisp supports, enter a model yourself, and add a key now or later. Keys are saved in your operating system’s secure keychain, never in your profile file.",
        )
        self._provider = QComboBox()
        self._provider.addItem("I’ll choose later", "")
        from ui.settings_panel.dialog import _PROVIDER_LABELS

        for provider in _PROVIDER_DEFAULTS:
            self._provider.addItem(_PROVIDER_LABELS.get(provider, provider), provider)
        self._provider.currentIndexChanged.connect(self._update_provider_hint)
        self._provider_model = QComboBox()
        self._provider_model.setEditable(True)
        self._provider_model.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._provider_model.setPlaceholderText("Model name")
        self._custom_base_url = QLineEdit()
        self._custom_base_url.setPlaceholderText("Custom OpenAI-compatible endpoint URL, e.g. http://localhost:1234/v1")
        self._provider_key = QLineEdit()
        self._provider_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._provider_key.setPlaceholderText("API key (optional)")
        self._provider_hint = QLabel()
        self._provider_hint.setWordWrap(True)
        self._provider_hint.setStyleSheet("color: palette(placeholder-text);")
        layout.addWidget(self._provider)
        layout.addWidget(self._provider_model)
        layout.addWidget(self._custom_base_url)
        layout.addWidget(self._provider_key)
        layout.addWidget(self._provider_hint)
        layout.addStretch()
        self._update_provider_hint()
        self._pages.addWidget(page)

        page, layout = self._page(
            "Try a sign-in instead",
            "A ChatGPT Plus or Pro subscription can be connected without pasting an API key. This is optional — you can also finish setup and configure a provider later.",
        )
        self._oauth_status = QLabel("Not connected")
        self._oauth_status.setWordWrap(True)
        self._oauth_status.setStyleSheet("color: palette(placeholder-text);")
        self._oauth_button = QPushButton("Sign in with ChatGPT")
        self._oauth_button.clicked.connect(self._start_oauth)
        layout.addWidget(self._oauth_status)
        layout.addWidget(self._oauth_button)
        layout.addStretch()
        self._pages.addWidget(page)

        page, layout = self._page(
            "Voice preferences",
            "Choose what you would like to try first. Local options use on-device speech models; cloud options stay as a preference until you add the matching credentials in Settings.",
        )
        tts_label = QLabel("Would you like Wisp to speak replies?")
        self._tts = QComboBox()
        self._tts.addItem("Not now", "none")
        self._tts.addItem("Local voice — Kokoro (installer opens after setup)", "local")
        self._tts.addItem("Cloud voice — configure in Settings", "cloud")
        stt_label = QLabel("Would you like to speak to Wisp?")
        self._stt = QComboBox()
        self._stt.addItem("Not now", "none")
        self._stt.addItem("Local speech recognition — Whisper (installer opens after setup)", "local")
        self._stt.addItem("Cloud/live voice — configure in Settings", "cloud")
        layout.addWidget(tts_label)
        layout.addWidget(self._tts)
        layout.addWidget(stt_label)
        layout.addWidget(self._stt)
        layout.addStretch()
        self._pages.addWidget(page)

        page, layout = self._page(
            "You’re ready to try Wisp",
            "Your profile is ready. Use the floating icon whenever you need help, and open chat only when it suits the task. You can revisit every choice in Settings.",
        )
        self._trial_steps = QLabel()
        self._trial_steps.setWordWrap(True)
        self._trial_steps.setFrameShape(QFrame.Shape.StyledPanel)
        self._trial_steps.setContentsMargins(14, 14, 14, 14)
        self._open_chat = QCheckBox("Open a new chat after setup")
        layout.addWidget(self._trial_steps)
        layout.addWidget(self._open_chat)
        layout.addStretch()
        self._pages.addWidget(page)

    def _is_advanced(self) -> bool:
        return self._advanced_mode.isChecked()

    def _page_sequence(self) -> list[int]:
        return [0, 1, 2, *([3] if self._is_advanced() else []), 4, 5, 6]

    def _show_page(self, index: int) -> None:
        sequence = self._page_sequence()
        self._pages.setCurrentIndex(index)
        position = sequence.index(index) + 1
        self._progress.setText(t("Step {current} of {total}").format(current=position, total=len(sequence)))
        self._back.setEnabled(position > 1)
        self._next.setText(t("Finish setup") if index == sequence[-1] else t("Continue"))
        self._update_navigation()

    def _preview_app_language(self) -> None:
        """Translate the remaining wizard pages as soon as a language is chosen."""
        try:
            import config
            from ui import i18n

            language = str(self._app_language.currentData() or "")
            config.APP_LANGUAGE = language
            i18n.set_language(language, app=QApplication.instance())
            localize_widget_tree(self)
            self._show_page(self._pages.currentIndex())
            self._refresh_trial_steps()
        except Exception:
            pass

    def _preview_theme(self) -> None:
        """Apply the selected color theme without waiting for setup to finish."""
        try:
            import config
            from ui.shared.theme import apply_app_theme

            mode = str(self._theme_mode.currentData() or "system")
            config.THEME_MODE = mode
            config.DARK_MODE = mode == "dark"
            apply_app_theme(QApplication.instance())
        except Exception:
            pass

    def reject(self) -> None:
        """Restore the running app language when setup is cancelled."""
        try:
            import config
            from ui import i18n

            config.APP_LANGUAGE = self._previous_app_language
            config.THEME_MODE = self._previous_theme_mode
            config.DARK_MODE = self._previous_theme_mode == "dark"
            i18n.set_language(self._previous_app_language, app=QApplication.instance())
            from ui.shared.theme import apply_app_theme

            apply_app_theme(QApplication.instance())
            localize_widget_tree(self)
        except Exception:
            pass
        super().reject()

    def _update_navigation(self) -> None:
        current = self._pages.currentIndex()
        self._next.setEnabled(current != 2 or bool(clean_profile_name(self._name.text())))

    def _go_back(self) -> None:
        sequence = self._page_sequence()
        current = self._pages.currentIndex()
        self._show_page(sequence[sequence.index(current) - 1])

    def _go_next(self) -> None:
        sequence = self._page_sequence()
        current = self._pages.currentIndex()
        if current == 2 and not clean_profile_name(self._name.text()):
            self._name.setFocus()
            return
        if current == sequence[-1]:
            self._finish()
            return
        self._show_page(sequence[sequence.index(current) + 1])

    def _update_provider_hint(self) -> None:
        provider = str(self._provider.currentData() or "")
        from ui.settings_panel.dialog import _MODEL_HINTS, _PROVIDER_MODELS

        previous_model = self._provider_model.currentText().strip()
        self._provider_model.blockSignals(True)
        self._provider_model.clear()
        self._provider_model.addItems(_PROVIDER_MODELS.get(provider, []))
        self._provider_model.setCurrentText(previous_model or _PROVIDER_DEFAULTS.get(provider, ""))
        self._provider_model.setPlaceholderText(t(_MODEL_HINTS.get(provider, "Enter a model name")))
        self._provider_model.blockSignals(False)
        self._provider_model.setEnabled(bool(provider))
        self._custom_base_url.setVisible(provider == "custom")
        if provider == "ollama":
            self._provider_key.setEnabled(False)
            self._provider_key.clear()
            self._provider_hint.setText(t("Ollama runs locally and does not use an API key. Enter the local model name you have installed."))
        elif provider:
            self._provider_key.setEnabled(True)
            self._provider_hint.setText(t("Model names are editable. You can leave the key blank and add it later in Settings."))
        else:
            self._provider_key.setEnabled(False)
            self._provider_key.clear()
            self._provider_model.clear()
            self._provider_model.setEnabled(False)
            self._custom_base_url.setVisible(False)
            self._provider_hint.setText(t("No provider will be changed yet. The ChatGPT sign-in on the next step is another option."))

    @staticmethod
    def _primary_hotkey_label() -> str:
        """Display the actual platform default rather than a Windows-only hint."""
        hotkey = "ctrl+q" if sys.platform == "win32" else "ctrl+alt+space"
        return "+".join(part.capitalize() for part in hotkey.split("+"))

    def _refresh_trial_steps(self) -> None:
        """Translate the platform-specific trial guidance after a language change."""
        if not hasattr(self, "_trial_steps"):
            return
        template = (
            "• Click the floating icon, or press {hotkey}, to ask about selected text or your current context.\n"
            "• Use the chat window for longer conversations, files, and history when you want it.\n"
            "• If you chose local speech, use F9 to talk and F7 to read selected text aloud after its first download."
        )
        self._trial_steps.setText(t(template).format(hotkey=self._primary_hotkey_label()))

    def _start_oauth(self) -> None:
        if self._oauth_in_progress:
            return
        self._oauth_in_progress = True
        self._oauth_error = ""
        self._oauth_button.setEnabled(False)
        self._oauth_status.setText(t("Opening your browser… finish the sign-in there, then return here."))
        self._oauth_status.setStyleSheet("color: palette(highlight);")
        try:
            from core.auth import chatgpt as chatgpt_auth

            def on_success(_tokens) -> None:
                self._oauth_connected = True

            def on_error(message: str) -> None:
                # The auth callback is invoked by a worker thread. Let the Qt
                # timer below update widgets on the GUI thread.
                self._oauth_error = str(message)
                self._oauth_in_progress = False

            chatgpt_auth.start_browser_login(on_success, on_error)
            self._oauth_poll_attempts = 0
            self._oauth_timer = QTimer(self)
            self._oauth_timer.setInterval(1000)
            self._oauth_timer.timeout.connect(self._poll_oauth)
            self._oauth_timer.start()
        except Exception as exc:  # noqa: BLE001 - shown in the wizard
            self._oauth_status.setText(t("Sign-in could not start: {error}").format(error=exc))
            self._oauth_status.setStyleSheet("color: #c04040;")
            self._oauth_in_progress = False
            self._oauth_button.setEnabled(True)

    def _poll_oauth(self) -> None:
        if self._oauth_error:
            self._oauth_timer.stop()
            self._oauth_status.setText(t("Sign-in could not start: {error}").format(error=self._oauth_error))
            self._oauth_status.setStyleSheet("color: #c04040;")
            self._oauth_button.setEnabled(True)
            return
        try:
            from core.auth import chatgpt as chatgpt_auth

            if chatgpt_auth.get_tokens():
                self._oauth_connected = True
                self._oauth_in_progress = False
                self._oauth_timer.stop()
                self._oauth_status.setText(t("Connected. Wisp will use your ChatGPT sign-in unless you selected another provider."))
                self._oauth_status.setStyleSheet("color: #408040;")
                self._oauth_button.setText(t("Connected"))
                return
        except Exception:
            pass
        self._oauth_poll_attempts += 1
        if self._oauth_poll_attempts >= 300:
            self._oauth_timer.stop()
            self._oauth_in_progress = False
            self._oauth_button.setEnabled(True)
            self._oauth_status.setText(t("Still not connected. You can continue and try again from Settings later."))
            self._oauth_status.setStyleSheet("color: palette(placeholder-text);")

    def _finish(self) -> None:
        if not clean_profile_name(self._name.text()):
            self._provider_hint.setText(t("Enter a valid profile name before finishing setup."))
            self._provider_hint.setStyleSheet("color: #c04040;")
            return
        provider = str(self._provider.currentData() or "") if self._is_advanced() else ""
        model = self._provider_model.currentText().strip() if self._is_advanced() else ""
        custom_base_url = self._custom_base_url.text().strip() if provider == "custom" else ""
        if provider == "custom":
            parsed = urlparse(custom_base_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                self._provider_hint.setText(t("Enter a valid HTTP(S) custom endpoint URL."))
                self._provider_hint.setStyleSheet("color: #c04040;")
                return
        key = self._provider_key.text().strip() if self._is_advanced() else ""
        if key and provider in _PROVIDER_SECRET_NAMES:
            key_name = _PROVIDER_SECRET_NAMES[provider]
            if key_name:
                try:
                    secret_store.set_secret(key_name, key)
                except Exception as exc:  # noqa: BLE001 - preserve the rest of setup
                    self._provider_hint.setText(t("Profile saved, but the key could not be stored: {error}").format(error=exc))
                    self._provider_hint.setStyleSheet("color: #c04040;")
                    return
        tts_preference = str(self._tts.currentData() or "none")
        stt_preference = str(self._stt.currentData() or "none")
        values = profile_values(
            name=self._name.text(),
            setup_mode="advanced" if self._is_advanced() else "simple",
            provider=provider,
            model=model,
            custom_base_url=custom_base_url,
            oauth_connected=self._oauth_connected,
            tts_preference=tts_preference,
            stt_preference=stt_preference,
            app_language=str(self._app_language.currentData() or ""),
            assistant_language=str(self._assistant_language.currentData() or ""),
            theme_mode=str(self._theme_mode.currentData() or "system"),
        )
        speech_install_request = local_speech_install_request(
            tts_preference=tts_preference,
            stt_preference=stt_preference,
            settings=values,
        )
        try:
            existing_env = settings_env.read_settings_env()
            values = persisted_profile_setup_values(
                name=self._name.text(),
                setup_values=values,
                existing_env=existing_env,
            )
            settings_env.write_settings_env(values)
        except Exception as exc:  # noqa: BLE001 - keep setup open and retryable
            self._provider_hint.setText(
                t("Profile settings could not be saved: {error}").format(error=exc)
            )
            self._provider_hint.setStyleSheet("color: #c04040;")
            return
        self.open_chat_requested = self._open_chat.isChecked()
        if self._on_complete:
            self._on_complete(self.open_chat_requested)
        self.accept()
        if speech_install_request is not None:
            QTimer.singleShot(0, lambda request=speech_install_request: launch_local_speech_installer(request))
