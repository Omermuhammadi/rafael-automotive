"""Tests for binary_studio.hexview — row slicing, offsets, hex/ASCII rendering."""

from __future__ import annotations

import pytest

from samples import make_sample as ms
from src.binary_studio import hexview
from src.binary_studio.hexview import HexRow


def test_rows_count_offsets_and_partial_last_row() -> None:
    buf = bytes(range(40))  # 40 bytes -> at width 16: rows of 16, 16, 8
    got = hexview.rows(buf, start=0, count=10, width=16)
    assert len(got) == 3
    assert [r.offset for r in got] == [0, 16, 32]
    assert got[0].data == bytes(range(0, 16))
    assert got[2].data == bytes(range(32, 40))  # short final row


def test_rows_start_past_end_is_empty() -> None:
    assert hexview.rows(b"abc", start=99, count=5) == []


def test_rows_respects_count_cap() -> None:
    buf = bytes(256)
    got = hexview.rows(buf, start=0, count=2, width=16)
    assert len(got) == 2


def test_total_rows_and_offset_digits() -> None:
    assert hexview.total_rows(b"", 16) == 0
    assert hexview.total_rows(bytes(16), 16) == 1
    assert hexview.total_rows(bytes(17), 16) == 2
    assert hexview.offset_digits_for(bytes(0x100000)) == 6  # 0x0FFFFF -> 5 digits -> min 6


def test_ascii_and_hex_cells() -> None:
    row = HexRow(offset=0, data=b"AB\x00\xff")
    assert row.ascii_cells() == "AB.."
    # width 16 keeps column spacing for the short row
    cells = row.hex_cells(width=16)
    assert cells.startswith("41 42 00 FF")


def test_render_line_shape() -> None:
    row = HexRow(offset=0x1A2F0, data=bytes.fromhex("AABBCCDDEEFF0011"))
    line = row.render(width=16, offset_digits=6)
    assert line.startswith("01A2F0  AA BB CC DD")
    assert line.endswith("|........|")  # non-printable -> dots, delimited by pipes


def test_rows_over_synthetic_show_calid_bytes() -> None:
    buf = ms.build_sample()
    # Row starting at the CALID offset holds the ASCII identifier.
    (row,) = hexview.rows(buf, start=ms.CALID_OFFSET, count=1, width=16)
    assert row.ascii_cells().startswith("1267394012")


def test_bad_width_raises() -> None:
    with pytest.raises(ValueError):
        hexview.rows(b"abc", 0, 1, width=0)
