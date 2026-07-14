"""GUI tests for the Binary Studio panel: load populates hex view + identifiers.

Skips when no display is available (headless CI); the logic is covered separately by the
binfile / hexview / identifiers tests.
"""

from __future__ import annotations

import time
import tkinter

import pytest

from samples import make_sample as ms
from src.app.shell import build_app
from src.binary_studio.panel import BACKEND_J2534


def _wait(app, cond, timeout: float = 8.0) -> None:
    end = time.time() + timeout
    while time.time() < end:
        app.update()
        time.sleep(0.03)
        if cond():
            return
    raise AssertionError("condition not met within timeout")


def _app():
    try:
        return build_app()
    except tkinter.TclError as exc:  # no display
        pytest.skip(f"no display for Tk: {exc}")


def test_load_populates_hexview_and_identifiers(tmp_path) -> None:
    app = _app()
    try:
        sample = ms.write_sample(tmp_path / "synthetic_ecu.bin")
        panel = app.binary_panel
        assert panel.load_path(sample) is True
        app.update()

        texts = [lbl.cget("text") for lbl in panel._id_rows]  # noqa: SLF001 — white-box UI check
        assert any("1267394012" in t for t in texts), texts
        assert any("4A8B2C1E" in t for t in texts), texts
        assert len(panel._buf) == ms.SIZE  # noqa: SLF001
    finally:
        app.destroy()


def test_goto_offset_scrolls_to_row(tmp_path) -> None:
    app = _app()
    try:
        sample = ms.write_sample(tmp_path / "s.bin")
        panel = app.binary_panel
        panel.load_path(sample)
        app.update()
        panel.hexview.goto_offset(ms.CALID_OFFSET)
        app.update()
        assert panel.hexview._top_row == ms.CALID_OFFSET // 16  # noqa: SLF001
    finally:
        app.destroy()


def test_load_missing_file_reports_failure() -> None:
    app = _app()
    try:
        assert app.binary_panel.load_path("nope/missing.bin") is False
        app.update()
    finally:
        app.destroy()


def test_full_patch_checksum_workflow(tmp_path) -> None:
    """Drive the Pillar-A software flow through the panel: load -> patch -> FAIL -> repair -> PASS -> export."""
    app = _app()
    try:
        sample = ms.write_sample(tmp_path / "synthetic_ecu.bin")
        panel = app.binary_panel
        panel.load_path(sample)
        panel.load_patches(ms.DEFAULT_PATH.parent / "example_patches.json")
        app.update()

        # Fresh image validates.
        assert panel.validate_checksum().severity.value == "ok"

        # Apply the first demo patch, then the checksum must fail.
        assert panel.apply_selected_patch() is True
        assert panel.validate_checksum().severity.value == "fail"

        # Repair, then it passes again.
        assert panel.patch_checksum().severity.value == "ok"
        assert panel.validate_checksum().severity.value == "ok"

        # Export and reload — the patched bytes and a valid checksum survive the round-trip.
        out = tmp_path / "patched_ecu_release.bin"
        assert panel.export_to(out) is True
        from src.binary_studio import binfile, checksum
        reloaded = binfile.load(out)
        assert reloaded[0x1A2F0:0x1A2F0 + 8] == bytes.fromhex("0011223344556677")
        assert checksum.validate(reloaded, slice(0, ms.SIZE - 4),
                                 slice(ms.SIZE - 4, ms.SIZE), "crc32").severity.value == "ok"
    finally:
        app.destroy()


def test_apply_disabled_until_file_and_patches(tmp_path) -> None:
    app = _app()
    try:
        panel = app.binary_panel
        assert str(panel._apply_btn.cget("state")) == "disabled"  # noqa: SLF001
        panel.load_path(ms.write_sample(tmp_path / "s.bin"))
        panel.load_patches(ms.DEFAULT_PATH.parent / "example_patches.json")
        app.update()
        assert str(panel._apply_btn.cget("state")) == "normal"  # noqa: SLF001
    finally:
        app.destroy()


def test_hardware_initialize_extract_writeback(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)  # keep original_ecu_dump.bin out of the repo
    app = _app()
    panel = app.binary_panel
    try:
        panel._on_initialize()  # noqa: SLF001 — Mock ECU backend by default
        _wait(app, lambda: panel._transport is not None)  # noqa: SLF001
        assert str(panel._extract_btn.cget("state")) == "normal"  # noqa: SLF001

        panel._on_extract()  # noqa: SLF001
        _wait(app, lambda: len(panel._buf) == 0x100000)  # noqa: SLF001
        assert (tmp_path / "original_ecu_dump.bin").exists()

        panel._on_write()  # noqa: SLF001
        _wait(app, lambda: "complete" in panel._hw_status.cget("text")  # noqa: SLF001
              or "failed" in panel._hw_status.cget("text"))
        assert "complete" in panel._hw_status.cget("text")  # noqa: SLF001
    finally:
        app.destroy()


def test_write_before_init_is_guarded() -> None:
    app = _app()
    panel = app.binary_panel
    try:
        panel._on_write()  # noqa: SLF001 — no transport, no buffer
        app.update()
        status = panel._hw_status.cget("text").lower()  # noqa: SLF001
        assert "initialize" in status or "first" in status
    finally:
        app.destroy()


def test_j2534_backend_reports_no_driver() -> None:
    app = _app()
    panel = app.binary_panel
    try:
        panel._conn_menu.set(BACKEND_J2534)  # noqa: SLF001
        panel._on_initialize()  # noqa: SLF001
        _wait(app, lambda: panel._transport is None  # noqa: SLF001
              and str(panel._init_btn.cget("state")) == "normal")  # noqa: SLF001
        status = panel._hw_status.cget("text").lower()  # noqa: SLF001
        assert "driver" in status or "windows" in status or "not found" in status
    finally:
        app.destroy()
