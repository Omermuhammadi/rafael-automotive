"""Tests for binary_studio.patches — loading defs and applying at offset with verification.

Includes the end-to-end guarantee of Pillar A's software half: load -> patch -> checksum
FAIL -> repair -> PASS on the synthetic image.
"""

from __future__ import annotations

import pytest

from samples import make_sample as ms
from src.binary_studio import checksum, patches
from src.binary_studio.patches import Patch
from src.core.findings import Severity

EXAMPLE_PATCHES = ms.DEFAULT_PATH.parent / "example_patches.json"


def test_load_example_patch_defs() -> None:
    defs = patches.load_patch_defs(EXAMPLE_PATCHES)
    assert len(defs) == 2
    first = defs[0]
    assert first.id == "reserved_region_override"
    assert first.offset == 0x1A2F0
    assert first.data == bytes.fromhex("0011223344556677")
    assert first.original == bytes.fromhex("AABBCCDDEEFF0011")


def test_apply_patch_success_mutates_buffer() -> None:
    buf = bytearray(ms.build_sample())
    (patch, _) = patches.load_patch_defs(EXAMPLE_PATCHES)
    f = patches.apply_patch(buf, patch)
    assert f.severity is Severity.OK
    assert buf[0x1A2F0:0x1A2F0 + 8] == bytes.fromhex("0011223344556677")


def test_apply_patch_original_mismatch_is_rejected_and_buffer_untouched() -> None:
    buf = bytearray(ms.build_sample())
    bad = Patch(id="wrong", offset=0x1A2F0, data=b"\x00\x00",
                original=b"\x12\x34")  # wrong expected bytes
    before = bytes(buf[0x1A2F0:0x1A2F0 + 2])
    f = patches.apply_patch(buf, bad)
    assert f.severity is Severity.FAIL
    assert "mismatch" in f.title
    assert bytes(buf[0x1A2F0:0x1A2F0 + 2]) == before  # unchanged


def test_apply_patch_skip_verification() -> None:
    buf = bytearray(ms.build_sample())
    p = Patch(id="noverify", offset=0x1A2F0, data=b"\xAB\xCD", original=b"\x00\x00")
    f = patches.apply_patch(buf, p, verify_original=False)
    assert f.severity is Severity.OK
    assert buf[0x1A2F0:0x1A2F0 + 2] == b"\xAB\xCD"


def test_apply_patch_out_of_range() -> None:
    buf = bytearray(16)
    p = Patch(id="oob", offset=0x100, data=b"\x00")
    assert patches.apply_patch(buf, p).severity is Severity.FAIL


def test_bad_length_patch_file_raises(tmp_path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text('{"patches":[{"id":"x","offset":"0x0","original":"AA BB","bytes":"00"}]}')
    with pytest.raises(ValueError):
        patches.load_patch_defs(bad)


def test_missing_patch_file_raises() -> None:
    with pytest.raises(FileNotFoundError):
        patches.load_patch_defs("nope/missing_patches.json")


def test_invalid_json_patch_file_raises(tmp_path) -> None:
    bad = tmp_path / "notjson.json"
    bad.write_text("{not valid json")
    with pytest.raises(ValueError):
        patches.load_patch_defs(bad)


def test_end_to_end_patch_then_checksum_repair() -> None:
    """The Pillar-A software guarantee: apply -> validate FAIL -> repair -> validate PASS -> export bytes."""
    buf = bytearray(ms.build_sample())
    region, stored = slice(0, ms.SIZE - 4), slice(ms.SIZE - 4, ms.SIZE)

    # Fresh image validates.
    assert checksum.validate(buf, region, stored, "crc32").severity is Severity.OK

    # Apply the demo patch -> checksum now mismatches.
    (patch, _) = patches.load_patch_defs(EXAMPLE_PATCHES)
    assert patches.apply_patch(buf, patch).severity is Severity.OK
    assert checksum.validate(buf, region, stored, "crc32").severity is Severity.FAIL

    # Repair -> validates again; the patched bytes persist.
    assert checksum.repair(buf, region, stored, "crc32").severity is Severity.OK
    assert checksum.validate(buf, region, stored, "crc32").severity is Severity.OK
    assert buf[0x1A2F0:0x1A2F0 + 8] == bytes.fromhex("0011223344556677")
