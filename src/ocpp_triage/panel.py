"""Tab 3 — OCPP Sniffer panel (mock CSMS + sim CP + colour-coded log + inspector + reset).

Phase 3 delivered the proxy, the simulated charge point, and the live frame log. Phase 4 adds
the full fault-scenario dropdown (8 scenarios), the click-a-line JSON deep-dive inspector, and
the Force Remote Station Reset command. The async OCPP work runs on a background event loop
(:class:`AsyncLoopThread`) and reports through the shared log bus; the shell drains it on the UI
thread, where this panel paints the frames and answers clicks.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
import time
import tkinter as tk
from collections.abc import Callable
from tkinter import font as tkfont

import customtkinter as ctk

from src.app import theme
from src.core.findings import Finding, Severity, Source
from src.core.logbus import LogBus
from src.ocpp_triage import inspector
from src.ocpp_triage.csms import DEFAULT_PORT, CsmsPolicy, MockCsmsServer
from src.ocpp_triage.scenarios import (
    SCENARIOS,
    SCENARIOS_BY_ID,
    SCENARIOS_BY_NAME,
    ScenarioContext,
)

_SEVERITY_TAG = {Severity.OK: "ok", Severity.INFO: "info", Severity.WARN: "warn", Severity.FAIL: "fail"}
_MAX_LOG_LINES = 1500


class AsyncLoopThread:
    """A dedicated asyncio event loop running on its own daemon thread."""

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, name="ocpp-async", daemon=True)
        self._started = False

    def start(self) -> None:
        if not self._started:
            self._thread.start()
            self._started = True

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def submit(self, coro) -> concurrent.futures.Future:
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def stop(self) -> None:
        if self._started:
            self._loop.call_soon_threadsafe(self._loop.stop)


class OcppSnifferPanel(ctk.CTkFrame):
    """Mock CSMS + simulated charge point + live colour-coded frame log + JSON inspector."""

    def __init__(self, parent: ctk.CTkBaseClass, logbus: LogBus | None = None) -> None:
        super().__init__(parent, fg_color="transparent")
        self.logbus = logbus
        self._loop: AsyncLoopThread | None = None
        self._server: MockCsmsServer | None = None
        self._log_findings: list[Finding] = []
        self._build()
        if logbus is not None:
            logbus.subscribe(self._on_finding)

    # ── construction ──────────────────────────────────────────────────────────
    def _build(self) -> None:
        self.grid_rowconfigure(2, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=0)
        self._build_server_bar()
        self._build_session_bar()
        self._build_log()
        self._build_inspector()

    def _build_server_bar(self) -> None:
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=0, column=0, columnspan=2, sticky="ew", padx=12, pady=(12, 4))

        self._start_btn = ctk.CTkButton(bar, text="Start Central System Proxy", width=190,
                                        command=self._on_start)
        self._start_btn.pack(side="left")
        self._stop_btn = ctk.CTkButton(bar, text="Stop", width=64, command=self._on_stop,
                                       fg_color="transparent", border_width=1, state="disabled")
        self._stop_btn.pack(side="left", padx=(8, 0))
        self._status = ctk.CTkLabel(bar, text="Proxy stopped.", font=("Segoe UI", 12),
                                    text_color=theme.TEXT_MUTED)
        self._status.pack(side="left", padx=14)
        ctk.CTkButton(bar, text="Clear log", width=80, fg_color="transparent", border_width=1,
                      command=self._clear_log).pack(side="right")

    def _build_session_bar(self) -> None:
        bar = ctk.CTkFrame(self, fg_color=theme.SURFACE, corner_radius=6)
        bar.grid(row=1, column=0, columnspan=2, sticky="ew", padx=12, pady=(4, 8))

        ctk.CTkLabel(bar, text="Scenario:", font=("Segoe UI", 12), text_color=theme.TEXT_MUTED).pack(
            side="left", padx=(12, 6), pady=8)
        self._scenario_menu = ctk.CTkOptionMenu(bar, width=240, values=[s.name for s in SCENARIOS])
        self._scenario_menu.set(SCENARIOS[0].name)
        self._scenario_menu.pack(side="left", pady=8)
        self._run_btn = ctk.CTkButton(bar, text="Run Scenario", width=130, command=self._on_run,
                                      state="disabled")
        self._run_btn.pack(side="left", padx=8, pady=8)

        self._reset_btn = ctk.CTkButton(bar, text="Force Remote Station Reset", width=210,
                                        command=self._on_reset, state="disabled",
                                        fg_color=theme.SEVERITY_COLORS[Severity.WARN],
                                        text_color="#1a1d21", hover_color="#d9a520")
        self._reset_btn.pack(side="right", padx=(8, 12), pady=8)

    def _build_log(self) -> None:
        frame = ctk.CTkFrame(self, fg_color=theme.BG, corner_radius=6)
        frame.grid(row=2, column=0, sticky="nsew", padx=(12, 6), pady=(0, 12))
        frame.grid_rowconfigure(0, weight=1)
        frame.grid_columnconfigure(0, weight=1)

        font = tkfont.Font(family="Consolas", size=12)
        self._log = tk.Text(frame, wrap="none", font=font, bg=theme.BG, fg=theme.TEXT_MUTED,
                            borderwidth=0, highlightthickness=0, padx=12, pady=10,
                            state="disabled", cursor="arrow")
        self._log.grid(row=0, column=0, sticky="nsew")
        for sev, tagname in _SEVERITY_TAG.items():
            self._log.tag_configure(tagname, foreground=theme.color_for(sev))
        self._log.tag_configure("sel_line", background="#2f3542")
        self._log.bind("<Button-1>", self._on_log_click)

        scroll = ctk.CTkScrollbar(frame, command=self._log.yview)
        scroll.grid(row=0, column=1, sticky="ns", padx=(2, 4), pady=4)
        self._log.configure(yscrollcommand=scroll.set)

        self._line_count = 0
        self._log_empty_hint()

    def _build_inspector(self) -> None:
        side = ctk.CTkFrame(self, fg_color=theme.SURFACE, corner_radius=6, width=360)
        side.grid(row=2, column=1, sticky="nsew", padx=(6, 12), pady=(0, 12))
        side.grid_propagate(False)
        side.grid_rowconfigure(2, weight=1)
        side.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(side, text="Frame Inspector", font=("Segoe UI", 14, "bold"),
                     text_color=theme.TEXT).grid(row=0, column=0, sticky="w", padx=14, pady=(14, 2))
        self._insp_title = ctk.CTkLabel(side, text="Click a frame in the log to inspect its JSON.",
                                        font=("Segoe UI", 12), text_color=theme.TEXT_MUTED,
                                        wraplength=320, justify="left", anchor="w")
        self._insp_title.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 8))

        json_font = tkfont.Font(family="Consolas", size=11)
        self._insp_json = tk.Text(side, wrap="none", font=json_font, bg=theme.BG, fg=theme.TEXT,
                                  borderwidth=0, highlightthickness=0, padx=10, pady=8,
                                  state="disabled", height=16, cursor="arrow")
        self._insp_json.grid(row=2, column=0, sticky="nsew", padx=12, pady=(0, 8))
        self._insp_json.tag_configure("hl", foreground=theme.SEVERITY_COLORS[Severity.FAIL],
                                      background="#2a2f38")

        self._insp_notes = ctk.CTkLabel(side, text="", font=("Segoe UI", 12),
                                        text_color=theme.TEXT_MUTED, wraplength=320,
                                        justify="left", anchor="nw")
        self._insp_notes.grid(row=3, column=0, sticky="ew", padx=14, pady=(0, 12))

    # ── log rendering (UI thread) ─────────────────────────────────────────────
    def _log_empty_hint(self) -> None:
        self._log.configure(state="normal")
        self._log.insert("1.0",
                         "  Start the Central System Proxy, then run a scenario.\n"
                         "  Green = accepted, orange = in flight, red = failed. Click a red line to inspect.\n")
        self._log.configure(state="disabled")

    def _on_finding(self, finding: Finding) -> None:
        if finding.source is not Source.OCPP:
            return
        if self._line_count == 0:
            self._log.configure(state="normal")
            self._log.delete("1.0", "end")
            self._log.configure(state="disabled")

        tag = _SEVERITY_TAG.get(finding.severity, "info")
        line = f"{time.strftime('%H:%M:%S')}   {finding.title}"
        if finding.severity is Severity.FAIL and finding.detail:
            line += f"   [{finding.detail}]"

        self._log.configure(state="normal")
        self._log.insert("end", line + "\n", tag)
        self._log_findings.append(finding)
        self._line_count += 1
        if self._line_count > _MAX_LOG_LINES:
            self._log.delete("1.0", "2.0")
            self._log_findings.pop(0)
            self._line_count -= 1
        self._log.configure(state="disabled")
        self._log.see("end")

    def _clear_log(self) -> None:
        self._log.configure(state="normal")
        self._log.delete("1.0", "end")
        self._log.configure(state="disabled")
        self._log_findings = []
        self._line_count = 0
        self._log_empty_hint()

    def _on_log_click(self, event: tk.Event) -> None:
        index = self._log.index(f"@{event.x},{event.y}")
        line = int(index.split(".")[0])
        if 1 <= line <= len(self._log_findings):
            self._log.tag_remove("sel_line", "1.0", "end")
            self._log.tag_add("sel_line", f"{line}.0", f"{line}.end")
            self._show_inspection(self._log_findings[line - 1])

    def _show_inspection(self, finding: Finding) -> None:
        result = inspector.inspect(finding)
        self._insp_title.configure(text=result.title, text_color=theme.color_for(result.severity))
        self._insp_json.configure(state="normal")
        self._insp_json.delete("1.0", "end")
        self._insp_json.insert("1.0", result.pretty_json)
        self._insp_json.tag_remove("hl", "1.0", "end")
        for needle in result.highlights:
            self._highlight(needle)
        self._insp_json.configure(state="disabled")
        self._insp_notes.configure(text="\n".join(f"•  {note}" for note in result.notes))

    def _highlight(self, needle: str) -> None:
        start = "1.0"
        while True:
            pos = self._insp_json.search(needle, start, stopindex="end")
            if not pos:
                break
            end = f"{pos}+{len(needle)}c"
            self._insp_json.tag_add("hl", pos, end)
            start = end

    # ── server / scenario control ──────────────────────────────────────────────
    def _ensure_loop(self) -> AsyncLoopThread:
        if self._loop is None:
            self._loop = AsyncLoopThread()
            self._loop.start()
        return self._loop

    def _context(self) -> ScenarioContext:
        return ScenarioContext(host=self._server.host, port=self._server.port,
                               logbus=self.logbus, server=self._server)

    def start_proxy(self) -> concurrent.futures.Future | None:
        if self._server is not None and self._server.running:
            return None
        loop = self._ensure_loop()
        if self._server is None:
            self._server = MockCsmsServer(self.logbus, policy=CsmsPolicy())
        return loop.submit(self._server.start())

    def run_scenario(self) -> concurrent.futures.Future | None:
        """Run the scenario selected in the dropdown against the running proxy."""
        if self._server is None or not self._server.running:
            self._set_status("Start the proxy first.", error=True)
            return None
        scenario = SCENARIOS_BY_NAME[self._scenario_menu.get()]
        self._server.policy = scenario.policy
        return self._ensure_loop().submit(scenario.flow(self._context()))

    def force_reset(self) -> concurrent.futures.Future | None:
        """Run the remote-reset flow (CSMS → CP Reset {'type':'Hard'}) against the proxy."""
        if self._server is None or not self._server.running:
            self._set_status("Start the proxy first.", error=True)
            return None
        scenario = SCENARIOS_BY_ID["remote_reset"]
        self._server.policy = scenario.policy
        return self._ensure_loop().submit(scenario.flow(self._context()))

    def stop_proxy(self) -> concurrent.futures.Future | None:
        if self._server is None or not self._server.running:
            return None
        return self._ensure_loop().submit(self._server.stop())

    def _port(self) -> int:
        return self._server.port if self._server else DEFAULT_PORT

    # ── button handlers ────────────────────────────────────────────────────────
    def _on_start(self) -> None:
        future = self.start_proxy()
        if future is None:
            return
        self._set_status("Starting proxy…")
        self._await(future, on_ok=lambda: self._set_server_running(True), on_err=self._on_start_error)

    def _on_stop(self) -> None:
        future = self.stop_proxy()
        if future is not None:
            self._await(future, on_ok=lambda: self._set_server_running(False))

    def _on_run(self) -> None:
        self._run_with_button(self.run_scenario(), self._run_btn)

    def _on_reset(self) -> None:
        self._run_with_button(self.force_reset(), self._reset_btn)

    def _run_with_button(self, future: concurrent.futures.Future | None,
                         button: ctk.CTkButton) -> None:
        if future is None:
            return
        button.configure(state="disabled")
        self._await(
            future,
            on_ok=lambda: button.configure(state="normal"),
            on_err=lambda exc: (self._set_status(f"Scenario error: {exc}", error=True),
                                button.configure(state="normal")),
        )

    def _on_start_error(self, exc: Exception) -> None:
        hint = f"Port {self._port()} already in use." if isinstance(exc, OSError) else str(exc)
        self._set_status(f"Could not start proxy: {hint}", error=True)

    def _set_server_running(self, running: bool) -> None:
        self._start_btn.configure(state="disabled" if running else "normal")
        for btn in (self._stop_btn, self._run_btn, self._reset_btn):
            btn.configure(state="normal" if running else "disabled")
        self._set_status(f"Listening on ws://localhost:{self._port()} (OCPP 1.6J)."
                         if running else "Proxy stopped.")

    def _set_status(self, text: str, error: bool = False) -> None:
        self._status.configure(
            text=text,
            text_color=theme.SEVERITY_COLORS[Severity.FAIL] if error else theme.TEXT_MUTED,
        )

    def _await(self, future: concurrent.futures.Future,
               on_ok: Callable[[], None], on_err: Callable[[Exception], None] | None = None) -> None:
        def check() -> None:
            if not future.done():
                self.after(50, check)
                return
            try:
                future.result()
            except Exception as exc:
                if on_err is not None:
                    on_err(exc)
            else:
                on_ok()
        self.after(50, check)

    def shutdown(self) -> None:
        """Best-effort clean stop of the server and loop (called on app close)."""
        try:
            if self._server is not None and self._server.running and self._loop is not None:
                self._loop.submit(self._server.stop())
                time.sleep(0.1)
        finally:
            if self._loop is not None:
                self._loop.stop()
                self._loop = None
