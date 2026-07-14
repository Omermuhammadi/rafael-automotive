"""Dark theme and the severity -> colour map for the UI.

This is a UI module (it imports CustomTkinter), so it is the right home for colours — the
logic modules in ``core``/``binary_studio``/``ocpp_triage`` stay UI-free. Importing this
module does not open a window; only building widgets needs a display.
"""

from __future__ import annotations

import customtkinter as ctk

from src.core.findings import Severity

APPEARANCE_MODE = "dark"
COLOR_THEME = "dark-blue"

# App palette (hex, theme-agnostic constants used by panels).
BG = "#1a1d21"
SURFACE = "#232830"
TEXT = "#e6e6e6"
TEXT_MUTED = "#9aa4b2"
ACCENT = "#3b8ed0"

# Severity -> row colour. Matches BUILD_PLAN: green / orange / amber / red.
SEVERITY_COLORS: dict[Severity, str] = {
    Severity.OK: "#2ecc71",    # green  — accepted / passed
    Severity.INFO: "#e67e22",  # orange — in flight
    Severity.WARN: "#f1c40f",  # amber  — non-fatal
    Severity.FAIL: "#e74c3c",  # red    — failure
}

# Monospace face for the hex viewer and JSON/frame panes.
MONO_FONT = ("Consolas", 12)


def color_for(severity: Severity) -> str:
    """Return the row colour for ``severity``."""
    return SEVERITY_COLORS[severity]


def apply() -> None:
    """Set CustomTkinter's global dark appearance. Call once before building the window."""
    ctk.set_appearance_mode(APPEARANCE_MODE)
    ctk.set_default_color_theme(COLOR_THEME)
