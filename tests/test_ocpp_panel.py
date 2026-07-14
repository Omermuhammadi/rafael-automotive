"""GUI tests for the OCPP Sniffer panel: proxy, scenarios, inspector, and remote reset.

Skips without a display; the async/logic layers are covered by test_ocpp.py / test_scenarios.py.
"""

from __future__ import annotations

import socket
import time
import tkinter

import pytest

from src.app.shell import build_app
from src.core.findings import Severity
from src.ocpp_triage.csms import CsmsPolicy, MockCsmsServer


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("localhost", 0))
        return s.getsockname()[1]


def _app():
    try:
        return build_app()
    except tkinter.TclError as exc:
        pytest.skip(f"no display for Tk: {exc}")


def _pump(app, n: int = 6) -> None:
    for _ in range(n):
        app.logbus.drain()
        app.update()
        time.sleep(0.05)


def test_panel_runs_scenario_and_populates_log() -> None:
    app = _app()
    panel = app.ocpp_panel
    try:
        panel._server = MockCsmsServer(app.logbus, port=_free_port(), policy=CsmsPolicy())  # noqa: SLF001
        panel.start_proxy().result(timeout=5)
        panel.run_scenario().result(timeout=10)  # default scenario = Happy path
        _pump(app)
        assert panel._line_count > 0  # noqa: SLF001
        panel.stop_proxy().result(timeout=5)
    finally:
        panel.shutdown()
        app.destroy()


def test_fault_scenario_then_inspect_red_line() -> None:
    app = _app()
    panel = app.ocpp_panel
    try:
        panel._server = MockCsmsServer(app.logbus, port=_free_port(), policy=CsmsPolicy())  # noqa: SLF001
        panel.start_proxy().result(timeout=5)
        panel._scenario_menu.set("Invalid auth token")  # noqa: SLF001
        panel.run_scenario().result(timeout=10)
        _pump(app)

        fails = [f for f in panel._log_findings if f.severity is Severity.FAIL]  # noqa: SLF001
        assert fails, "expected a red line from the invalid-auth scenario"

        panel._show_inspection(fails[0])  # noqa: SLF001 — the core of the click handler
        app.update()
        assert "Invalid" in panel._insp_json.get("1.0", "end")  # noqa: SLF001
        assert panel._insp_notes.cget("text")  # noqa: SLF001 — non-empty explanation
    finally:
        panel.shutdown()
        app.destroy()


def test_force_remote_reset_button() -> None:
    app = _app()
    panel = app.ocpp_panel
    try:
        panel._server = MockCsmsServer(app.logbus, port=_free_port())  # noqa: SLF001
        panel.start_proxy().result(timeout=5)
        panel.force_reset().result(timeout=10)
        _pump(app)
        titles = [f.title for f in panel._log_findings]  # noqa: SLF001
        assert any("Reset.req" in t for t in titles)
        assert any("rebooting" in t for t in titles)
    finally:
        panel.shutdown()
        app.destroy()
