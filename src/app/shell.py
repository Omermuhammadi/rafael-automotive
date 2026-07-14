"""The application shell: a dark, two-tab CustomTkinter window.

Phase 0 delivers the frame — a ``CTkTabview`` hosting the **Binary Studio** and
**OCPP Sniffer** panels — plus one shared :class:`~src.core.logbus.LogBus` and the Tk
``.after`` drain tick that later phases use to stream findings onto the UI thread.

Building the window needs a display; :func:`build_app` is separated from ``mainloop`` so
tests can construct-and-destroy it (and skip when no display is available).
"""

from __future__ import annotations

import customtkinter as ctk

from src.app import theme
from src.binary_studio.panel import BinaryStudioPanel
from src.core.logbus import LogBus
from src.ocpp_triage.panel import OcppSnifferPanel

WINDOW_TITLE = "Multi-Vehicle Diagnostic & Calibration Tool"
TAB_BINARY = "Binary Studio"
TAB_OCPP = "OCPP Sniffer"
DRAIN_INTERVAL_MS = 100


class AppShell(ctk.CTk):
    """Top-level window hosting the two in-scope pillars as tabs."""

    def __init__(self) -> None:
        super().__init__()
        self.logbus = LogBus()
        self._drain_after_id: str | None = None

        self.title(WINDOW_TITLE)
        self.geometry("1120x740")
        self.minsize(920, 620)
        self.configure(fg_color=theme.BG)

        self._build_tabs()
        self._schedule_drain()

    def _build_tabs(self) -> None:
        self.tabview = ctk.CTkTabview(self, fg_color=theme.SURFACE)
        self.tabview.pack(fill="both", expand=True, padx=12, pady=12)

        binary_tab = self.tabview.add(TAB_BINARY)
        ocpp_tab = self.tabview.add(TAB_OCPP)

        self.binary_panel = BinaryStudioPanel(binary_tab, self.logbus)
        self.binary_panel.pack(fill="both", expand=True)

        self.ocpp_panel = OcppSnifferPanel(ocpp_tab, self.logbus)
        self.ocpp_panel.pack(fill="both", expand=True)

        self.tabview.set(TAB_BINARY)

    def _schedule_drain(self) -> None:
        """Drain the log bus on the UI thread, then reschedule."""
        self.logbus.drain()
        self._drain_after_id = self.after(DRAIN_INTERVAL_MS, self._schedule_drain)

    def destroy(self) -> None:
        """Cancel the pending drain tick and stop the OCPP loop before teardown."""
        if self._drain_after_id is not None:
            try:
                self.after_cancel(self._drain_after_id)
            except Exception:
                pass
            self._drain_after_id = None
        ocpp_panel = getattr(self, "ocpp_panel", None)
        if ocpp_panel is not None:
            ocpp_panel.shutdown()
        super().destroy()


def build_app() -> AppShell:
    """Apply the theme and construct the window (does not enter the event loop)."""
    theme.apply()
    return AppShell()
