"""Tests for binary_studio.checksum — algorithms + validate/repair against the fixture."""

from __future__ import annotations

import pytest

from samples import make_sample as ms
from src.binary_studio import checksum
from src.core.findings import Severity


def test_algorithm_check_vectors() -> None:
    chk = b"123456789"
    assert checksum.crc32(chk) == 0xCBF43926   # standard CRC-32 check value
    assert checksum.crc16(chk) == 0x31C3       # CRC-16/XMODEM check value
    assert checksum.block_sum(b"\x01\x02\x03") == 6
    assert checksum.block_sum(b"\x01\x00\x02\x00", word=2) == 0x0003  # 0x0001 + 0x0002


def _spec():
    """The synthetic image's checksum spec: CRC32 over [0,0xFFFFC) stored big-endian at 0xFFFFC."""
    return slice(0, ms.SIZE - 4), slice(ms.SIZE - 4, ms.SIZE), "crc32", "big"


def test_validate_ok_on_fresh_image() -> None:
    buf = bytearray(ms.build_sample())
    region, stored, algo, endian = _spec()
    f = checksum.validate(buf, region, stored, algo, endian)
    assert f.severity is Severity.OK


def test_validate_fails_after_mutation_then_repair_fixes_it() -> None:
    buf = bytearray(ms.build_sample())
    region, stored, algo, endian = _spec()

    buf[0x1A2F0] ^= 0xFF  # corrupt a byte inside the checksum region
    assert checksum.validate(buf, region, stored, algo, endian).severity is Severity.FAIL

    repaired = checksum.repair(buf, region, stored, algo, endian)
    assert repaired.severity is Severity.OK
    # After repair the stored value must match a fresh computation, and validate must pass.
    assert checksum.validate(buf, region, stored, algo, endian).severity is Severity.OK
    assert int.from_bytes(buf[stored], "big") == checksum.crc32(bytes(buf[region]))


def test_repair_writes_expected_width_and_endianness() -> None:
    buf = bytearray(64)
    region, stored = slice(0, 60), slice(60, 64)
    checksum.repair(buf, region, stored, "crc32", "big")
    assert int.from_bytes(buf[stored], "big") == checksum.crc32(bytes(buf[region]))


def test_unknown_algo_raises() -> None:
    buf = bytearray(16)
    with pytest.raises(ValueError):
        checksum.validate(buf, slice(0, 12), slice(12, 16), "sha256")


def test_out_of_range_region_or_stored_is_rejected_without_corrupting() -> None:
    # Reachable from the UI: loading a sub-1 MB image against the 1 MB demo layout puts the
    # region/stored offsets past the end. repair() must not silently grow the buffer.
    buf = bytearray(64)
    v = checksum.validate(buf, slice(0, 60), slice(1000, 1004), "crc32")
    assert v.severity is Severity.FAIL and "out of range" in v.title.lower()

    before = bytes(buf)
    r = checksum.repair(buf, slice(0, 60), slice(1000, 1004), "crc32")
    assert r.severity is Severity.FAIL
    assert bytes(buf) == before  # buffer unchanged — no silent extend

    assert checksum.validate(buf, slice(0, 10_000), slice(60, 64), "crc32").severity is Severity.FAIL
