"""GUI smoke test: the shell builds with the two in-scope tabs.

Building CustomTkinter widgets needs a display. Where none is available (headless CI), the
test skips rather than fails — the logic tests carry the coverage that matters, and the app
is validated visually on the technician's Windows desktop.
"""

from __future__ import annotations

import tkinter

import pytest

from src.app.shell import TAB_BINARY, TAB_OCPP, build_app


def test_shell_builds_two_tabs() -> None:
    try:
        app = build_app()
    except tkinter.TclError as exc:  # no display available
        pytest.skip(f"no display for Tk: {exc}")

    try:
        app.update()  # force widget realisation without entering the event loop
        tab_names = list(app.tabview._tab_dict.keys())  # noqa: SLF001 — introspection in test
        assert TAB_BINARY in tab_names
        assert TAB_OCPP in tab_names
        assert app.tabview.get() == TAB_BINARY
        assert app.binary_panel is not None
        assert app.ocpp_panel is not None
    finally:
        app.destroy()
