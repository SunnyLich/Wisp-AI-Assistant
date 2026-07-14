"""
core/capture.py — Input capture: highlighted text and screen snippets.

Two modes:
  1. Text selection  — reads whatever the user has highlighted.
                       Tries Windows UI Automation first (no clipboard touch),
                       falls back to clipboard save/restore if UIA fails.
  2. Screen snippet  — takes a screenshot of the active monitor region.
"""
from __future__ import annotations

import io
import logging
import os
import sys
import time

import pyperclip
from PIL import Image

from core.system.main_thread import run_on_main

_IS_LINUX = sys.platform.startswith("linux")
_IS_MAC = sys.platform == "darwin"
_log = logging.getLogger("wisp.capture")

_PORTAL_BUS_NAME = "org.freedesktop.portal.Desktop"
_PORTAL_PATH = "/org/freedesktop/portal/desktop"
_PORTAL_SCREENSHOT_IFACE = "org.freedesktop.portal.Screenshot"
_PORTAL_REQUEST_IFACE = "org.freedesktop.portal.Request"
_PORTAL_TIMEOUT_SECONDS = 25.0


# ------------------------------------------------------------------
# UIA singleton — initialised once, reused across calls
# ------------------------------------------------------------------
_uia = None
_UIA_TextPatternId = 10014
_UIA_TextPatternRangeEndpoint_Start = 0
_UIA_TextPatternRangeEndpoint_End = 1


def _get_uia():
    """Return uia."""
    global _uia
    if _uia is None:
        try:
            import comtypes.client
            comtypes.client.GetModule("UIAutomationCore.dll")
            import comtypes.gen.UIAutomationClient as uiac  # type: ignore
            _uia = comtypes.client.CreateObject(
                "{ff48dba4-60ef-4201-aa87-54103eef594e}",
                interface=uiac.IUIAutomation,
            )
        except Exception:
            _uia = False   # mark as unavailable so we don't retry
    return _uia if _uia is not False else None


def _get_selected_text_uia() -> str | None:
    """Read selected text via Windows UI Automation — no clipboard involved."""
    uia = _get_uia()
    if uia is None:
        return None
    try:
        import comtypes.gen.UIAutomationClient as uiac  # type: ignore
        el = uia.GetFocusedElement()
        raw_pattern = el.GetCurrentPattern(_UIA_TextPatternId)
        tp = raw_pattern.QueryInterface(uiac.IUIAutomationTextPattern)
        selections = tp.GetSelection()
        if selections.Length == 0:
            return None
        start_endpoint = getattr(
            uiac,
            "TextPatternRangeEndpoint_Start",
            _UIA_TextPatternRangeEndpoint_Start,
        )
        end_endpoint = getattr(
            uiac,
            "TextPatternRangeEndpoint_End",
            _UIA_TextPatternRangeEndpoint_End,
        )
        selected_parts: list[str] = []
        for idx in range(selections.Length):
            text_range = selections.GetElement(idx)
            try:
                if text_range.CompareEndpoints(start_endpoint, text_range, end_endpoint) == 0:
                    continue
            except Exception:
                pass
            text = text_range.GetText(-1)
            text = text.strip() if text else ""
            if text:
                selected_parts.append(text)
        return "\n".join(selected_parts) or None
    except Exception:
        return None


def _get_selected_text_clipboard() -> str | None:
    """Fallback: Ctrl+C with save/restore so existing clipboard is preserved."""
    from core.platform_utils import send_keys, COPY_COMBO

    if _IS_MAC:
        from core.platform import macos_native

        # macos_native.get_selected_text holds the clipboard lock itself (it
        # is also called directly by native_host), so don't nest it here.
        return macos_native.get_selected_text(COPY_COMBO)

    from core.system import clipboard_lock

    # Serialize the save->copy->restore dance with any other Wisp-derived
    # process (e.g. the MCP context server) doing the same thing.
    with clipboard_lock.held():
        previous = _safe_get_clipboard()
        previous_sequence = _clipboard_sequence_number()
        send_keys(COPY_COMBO)
        text = ""
        changed = False
        deadline = time.monotonic() + (0.50 if sys.platform == "win32" else 0.18)
        while time.monotonic() < deadline:
            time.sleep(0.04)
            current = (_safe_get_clipboard() or "").strip()
            current_sequence = _clipboard_sequence_number()
            sequence_changed = (
                previous_sequence is not None
                and current_sequence is not None
                and current_sequence != previous_sequence
            )
            if current and (sequence_changed or current != (previous or "").strip()):
                text = current
                changed = True
                break
            if current and sequence_changed:
                text = current
                changed = True
                break

        # Restore original clipboard content
        if previous is not None:
            try:
                pyperclip.copy(previous)
            except Exception:
                pass

    return text if changed and text else None


def _clipboard_sequence_number() -> int | None:
    """Return the Windows clipboard sequence number when available."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        return int(ctypes.windll.user32.GetClipboardSequenceNumber())
    except Exception:
        return None


def _linux_x11_window_client_pid(display, window) -> int | None:
    """Resolve a window's owning pid via the X-Resource extension.

    Apps usually own PRIMARY through an invisible helper window that carries
    no _NET_WM_PID property, so asking the X server which client created the
    window is the only lookup that works for selection owners.
    """
    try:
        from Xlib.ext import res as xres

        reply = display.res_query_client_ids(
            [{"client": int(window.id), "mask": int(xres.LocalClientPIDMask)}]
        )
        for item in getattr(reply, "ids", None) or []:
            values = list(getattr(item, "value", None) or [])
            if values:
                return int(values[0])
    except Exception:
        return None
    return None


def _linux_x11_primary_selection_owner_pid() -> int | None:
    """Return the X11 PRIMARY owner pid when it can be verified."""
    try:
        from Xlib import X
        from Xlib.display import Display

        display = Display()
    except Exception:
        return None
    try:
        owner = display.get_selection_owner(display.intern_atom("PRIMARY"))
        if owner is None:
            return None
        pid = _linux_x11_window_client_pid(display, owner)
        if pid:
            return pid
        pid_atom = display.intern_atom("_NET_WM_PID")
        current = owner
        for _ in range(16):
            try:
                prop = current.get_full_property(pid_atom, X.AnyPropertyType)
                if prop is not None and len(prop.value):
                    return int(prop.value[0])
            except Exception:
                pass
            try:
                parent = current.query_tree().parent
            except Exception:
                break
            if parent is None or int(parent.id) == int(current.id):
                break
            current = parent
    finally:
        try:
            display.close()
        except Exception:
            pass
    return None


def _linux_x11_primary_selection_identity(timeout: float = 0.25) -> tuple[int, int] | None:
    """Return (owner window id, acquisition timestamp) for the X11 PRIMARY selection.

    The ICCCM-mandated TIMESTAMP target reports when the owner acquired
    PRIMARY. Re-selecting text - even the same text - acquires the selection
    again with a new timestamp, while a cleared highlight keeps the old one
    (X11 owners keep serving a selection after the user deselects). That makes
    the pair a stable identity for "has the user selected anything new since".
    Returns None when there is no owner or it never answers the request.
    """
    import select as select_module
    import time

    try:
        from Xlib import X
        from Xlib.display import Display

        display = Display()
    except Exception:
        return None
    try:
        primary = display.intern_atom("PRIMARY")
        owner = display.get_selection_owner(primary)
        owner_id = int(getattr(owner, "id", 0) or 0)
        if owner_id == 0:
            return None
        timestamp_atom = display.intern_atom("TIMESTAMP")
        property_atom = display.intern_atom("WISP_SELECTION_TIMESTAMP")
        screen = display.screen()
        window = screen.root.create_window(0, 0, 1, 1, 0, screen.root_depth)
        try:
            window.convert_selection(primary, timestamp_atom, property_atom, X.CurrentTime)
            display.flush()
            deadline = time.monotonic() + timeout
            while True:
                while display.pending_events():
                    ev = display.next_event()
                    if getattr(ev, "type", None) != X.SelectionNotify:
                        continue
                    if int(getattr(getattr(ev, "requestor", None), "id", 0) or 0) != int(window.id):
                        continue
                    if int(getattr(ev, "property", 0) or 0) == X.NONE:
                        return None  # owner refused the TIMESTAMP target
                    prop = window.get_full_property(property_atom, X.AnyPropertyType)
                    if prop is None or not len(prop.value):
                        return None
                    return owner_id, int(prop.value[0])
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                select_module.select([display.fileno()], [], [], remaining)
        finally:
            try:
                window.destroy()
                display.flush()
            except Exception:
                pass
    except Exception:
        return None
    finally:
        try:
            display.close()
        except Exception:
            pass


def _linux_processes_related(first_pid: int | None, second_pid: int | None) -> bool:
    """Return True when two Linux PIDs appear to belong to the same app tree."""
    first = int(first_pid or 0)
    second = int(second_pid or 0)
    if first <= 0 or second <= 0:
        return False
    if first == second:
        return True
    try:
        import psutil  # type: ignore

        first_proc = psutil.Process(first)
        second_proc = psutil.Process(second)
        first_ancestors = {int(proc.pid) for proc in first_proc.parents()}
        second_ancestors = {int(proc.pid) for proc in second_proc.parents()}
        return second in first_ancestors or first in second_ancestors
    except Exception:
        return False


def _get_primary_selection_linux(
    *,
    active_pid: int | None = None,
    require_active_owner: bool = False,
) -> str | None:
    """
    Read the X11/Wayland PRIMARY selection (the current highlight) on Linux.

    Highlighting text auto-fills PRIMARY, so this captures the selection without
    synthesising Ctrl+C — works even in terminals and apps that don't map copy,
    and never touches the regular clipboard. Tries the same backends pyperclip
    already relies on; returns None if none are installed or nothing is selected.
    """
    import os
    import subprocess

    if require_active_owner:
        if os.environ.get("WAYLAND_DISPLAY"):
            return None
        pid = int(active_pid or 0)
        if pid <= 0:
            return None
        owner_pid = _linux_x11_primary_selection_owner_pid()
        if owner_pid != pid and not _linux_processes_related(owner_pid, pid):
            return None

    xclip = ["xclip", "-selection", "primary", "-o"]
    xsel  = ["xsel", "-p"]
    wl    = ["wl-paste", "--primary", "--no-newline"]
    # Prefer the backend that matches the session type.
    commands = [wl, xclip, xsel] if os.environ.get("WAYLAND_DISPLAY") else [xclip, xsel, wl]

    for cmd in commands:
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=1.0)
        except Exception:
            continue
        if out.returncode == 0:
            text = (out.stdout or "").strip()
            if text:
                return text
    return None


def get_selected_text(*, allow_synthetic_copy: bool = True) -> str | None:
    """
    Returns the currently highlighted text.

    Windows: UIA (no clipboard touch), then Ctrl+C fallback.
    Linux:   PRIMARY selection (no keypress), then Ctrl+C fallback.
    macOS:   Ctrl+C fallback.

    allow_synthetic_copy=False skips the Ctrl+C/Cmd+C fallback entirely —
    callers that can't guarantee the target app has focus (e.g. the MCP
    context server, whose caller's own window is focused) use it so the copy
    keystroke never lands in the wrong window.
    """
    try:
        text = _get_selected_text_uia()
    except Exception:
        _log.exception("Selected-text UIA capture failed.")
        text = None
    if not text and _IS_LINUX and os.environ.get("WAYLAND_DISPLAY"):
        try:
            from core.platform import linux_atspi

            text = linux_atspi.get_selected_text()
        except Exception:
            _log.exception("Selected-text AT-SPI capture failed.")
            text = None
    if not text and _IS_LINUX:
        try:
            text = _get_primary_selection_linux()
        except Exception:
            _log.exception("Selected-text PRIMARY capture failed.")
            text = None
    if not text and allow_synthetic_copy and not (_IS_LINUX and os.environ.get("WAYLAND_DISPLAY")):
        try:
            text = _get_selected_text_clipboard()
        except Exception:
            _log.exception("Selected-text clipboard capture failed.")
            text = None
    return text


def get_screen_snippet(region: dict | None = None) -> Image.Image:
    """
    Captures a screenshot of the specified region or the primary monitor.

    Args:
        region: dict with keys top, left, width, height (mss format).
                If None, captures the entire primary monitor.

    Returns:
        PIL Image of the captured region.
    """
    if _IS_MAC:
        from tempfile import gettempdir
        from pathlib import Path
        from core.platform import macos_native

        out_path = Path(gettempdir()) / "wisp_screen_snippet.png"
        if macos_native.capture_screen_to_file(out_path, region=region):
            with Image.open(out_path) as img:
                return img.convert("RGB")
        raise RuntimeError("macOS screen capture failed")

    if _IS_LINUX and (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"):
        return _get_screen_snippet_wayland(region)

    # Windows/X11 path. mss is imported lazily so macOS does not load or drive
    # the Python CoreGraphics capture backend inside the Qt process.
    def _grab() -> Image.Image:
        """Handle grab for capture."""
        import mss

        mss_factory = getattr(mss, "MSS", mss.mss)
        with mss_factory() as sct:
            monitor = region if region else sct.monitors[1]  # monitors[1] = primary
            raw = sct.grab(monitor)
            return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")

    return run_on_main(_grab)


def _portal_variant_value(value):
    """Unwrap the ``(signature, value)`` representation Jeepney uses for variants."""
    if isinstance(value, tuple) and len(value) == 2 and isinstance(value[0], str):
        return value[1]
    return value


def _get_screen_snippet_wayland(region: dict | None = None) -> Image.Image:
    """Capture pixels through the Wayland desktop Screenshot portal.

    Native Wayland compositors intentionally reject X11 root-window capture.
    The portal is the compositor-approved path and may show a permission dialog.
    """
    import secrets
    import shutil
    import subprocess
    import tempfile
    from pathlib import Path
    from urllib.parse import unquote, urlparse

    spectacle = shutil.which("spectacle")
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    kde_session = "kde" in desktop or "plasma" in desktop or os.environ.get("KDE_FULL_SESSION") == "true"
    if spectacle and kde_session:
        output = Path(tempfile.gettempdir()) / f"wisp-wayland-{secrets.token_hex(8)}.png"
        try:
            result = subprocess.run(
                [spectacle, "--background", "--nonotify", "--fullscreen", "--output", str(output)],
                capture_output=True,
                text=True,
                timeout=8.0,
                check=False,
            )
            if result.returncode != 0 or not output.is_file() or output.stat().st_size <= 0:
                detail = (result.stderr or result.stdout or "capture produced no image").strip()
                raise RuntimeError(f"KDE Spectacle capture failed: {detail}")
            with Image.open(output) as source:
                image = source.convert("RGB")
                image.load()
            return _crop_capture_region(image, region)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("KDE Spectacle capture timed out") from exc
        finally:
            try:
                output.unlink(missing_ok=True)
            except OSError:
                pass

    try:
        from jeepney import DBusAddress, MatchRule, new_method_call
        from jeepney.bus_messages import message_bus
        from jeepney.io.blocking import open_dbus_connection
        from jeepney.low_level import HeaderFields
    except Exception as exc:
        raise RuntimeError("Wayland screenshot support requires Jeepney") from exc

    token = f"wisp_{secrets.token_hex(12)}"
    address = DBusAddress(
        _PORTAL_PATH,
        bus_name=_PORTAL_BUS_NAME,
        interface=_PORTAL_SCREENSHOT_IFACE,
    )
    rule = MatchRule(
        type="signal",
        sender=_PORTAL_BUS_NAME,
        interface=_PORTAL_REQUEST_IFACE,
        member="Response",
        path_namespace=f"{_PORTAL_PATH}/request",
    )

    conn = None
    rule_installed = False
    try:
        conn = open_dbus_connection(bus="SESSION")
        conn.send_and_get_reply(message_bus.AddMatch(rule))
        rule_installed = True
        with conn.filter(rule, bufsize=16) as responses:
            reply = conn.send_and_get_reply(
                new_method_call(
                    address,
                    "Screenshot",
                    "sa{sv}",
                    (
                        "",
                        {
                            "handle_token": ("s", token),
                            "interactive": ("b", False),
                            "modal": ("b", False),
                        },
                    ),
                ),
                timeout=10.0,
            )
            request_path = str(reply.body[0])
            deadline = time.monotonic() + min(_PORTAL_TIMEOUT_SECONDS, 8.0)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError("Wayland screenshot request timed out")
                response = conn.recv_until_filtered(responses, timeout=remaining)
                if response.header.fields.get(HeaderFields.path) == request_path:
                    break

        response_code = int(response.body[0])
        if response_code == 1:
            raise RuntimeError("Wayland screenshot request was cancelled")
        if response_code != 0:
            raise RuntimeError(f"Wayland screenshot request failed (portal response {response_code})")
        results = response.body[1] or {}
        uri = str(_portal_variant_value(results.get("uri", "")) or "")
        parsed = urlparse(uri)
        if parsed.scheme != "file" or not parsed.path:
            raise RuntimeError("Wayland screenshot portal returned no local image")
        path = unquote(parsed.path)
        with Image.open(path) as source:
            image = source.convert("RGB")
            image.load()
        return _crop_capture_region(image, region)
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"Wayland screenshot portal failed: {exc}") from exc
    finally:
        if conn is not None:
            if rule_installed:
                try:
                    conn.send_and_get_reply(message_bus.RemoveMatch(rule), timeout=2.0)
                except Exception:
                    pass
            try:
                conn.close()
            except Exception:
                pass


def _crop_capture_region(image: Image.Image, region: dict | None) -> Image.Image:
    """Crop a compositor capture using the existing mss-style region contract."""
    if not region:
        return image
    left = max(0, int(region.get("left", 0)))
    top = max(0, int(region.get("top", 0)))
    width = max(1, int(region.get("width", image.width - left)))
    height = max(1, int(region.get("height", image.height - top)))
    return image.crop((left, top, left + width, top + height))


def image_to_base64(img: Image.Image) -> str:
    """Encode a PIL Image as a base64 PNG string for LLM vision input."""
    import base64
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def get_clipboard_text() -> str | None:
    """
    Return the current clipboard text as context, without synthesising a copy
    keypress (unlike get_selected_text's fallback path).

    Cross-platform via pyperclip on Windows/Linux; on Linux it needs
    xclip/xsel (X11) or wl-clipboard (Wayland). If none is available pyperclip
    raises, which _safe_get_clipboard swallows — so this degrades to None
    rather than crashing.
    """
    if _IS_MAC:
        from core.platform import macos_native

        text = macos_native.get_clipboard_text()
    else:
        text = _safe_get_clipboard()
    if not text:
        return None
    text = text.strip()
    return text or None


def _safe_get_clipboard() -> str | None:
    """Handle safe get clipboard for capture."""
    try:
        return pyperclip.paste()
    except Exception:
        return None
