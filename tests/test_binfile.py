"""Tests for binary_studio.binfile — load/save across .bin/.hex/.s19 and sizing."""

from __future__ import annotations

import pytest

from samples import make_sample as ms
from src.binary_studio import binfile


def test_load_raw_bin_roundtrip(tmp_path) -> None:
    data = bytes(range(256)) * 4
    p = tmp_path / "image.bin"
    p.write_bytes(data)
    loaded = binfile.load(p)
    assert isinstance(loaded, bytearray)
    assert loaded == data
    assert binfile.size(loaded) == len(data)


def test_load_synthetic_is_one_mib() -> None:
    buf = binfile.load(ms.DEFAULT_PATH)
    assert binfile.size(buf) == 0x100000


def test_save_raw_bin_roundtrip(tmp_path) -> None:
    data = bytearray(b"\xde\xad\xbe\xef" * 10)
    p = tmp_path / "out.bin"
    binfile.save(p, data)
    assert binfile.load(p) == data


def test_intel_hex_roundtrip(tmp_path) -> None:
    data = bytes(range(64))
    p = tmp_path / "image.hex"
    binfile.save(p, data)
    text = p.read_text()
    assert text.startswith(":")  # Intel HEX records begin with a colon
    assert binfile.load(p) == data


def test_srecord_roundtrip(tmp_path) -> None:
    data = bytes(range(64))
    p = tmp_path / "image.s19"
    binfile.save(p, data)
    text = p.read_text()
    assert text.startswith("S")  # Motorola S-records begin with 'S'
    assert binfile.load(p) == data


def test_unknown_suffix_loads_raw(tmp_path) -> None:
    data = b"\x00\x01\x02not-a-known-format\xff"
    p = tmp_path / "image.rom"
    p.write_bytes(data)
    assert binfile.load(p) == data


def test_missing_file_raises() -> None:
    with pytest.raises(FileNotFoundError):
        binfile.load("this/does/not/exist.bin")


def test_malformed_hex_raises(tmp_path) -> None:
    # A .hex that is not valid Intel HEX must raise (the panel catches this and shows an error).
    p = tmp_path / "bad.hex"
    p.write_text("this is not intel hex\n")
    with pytest.raises(Exception):
        binfile.load(p)


def test_zero_byte_bin_loads_empty(tmp_path) -> None:
    p = tmp_path / "empty.bin"
    p.write_bytes(b"")
    assert binfile.load(p) == bytearray()
    assert binfile.size(binfile.load(p)) == 0
