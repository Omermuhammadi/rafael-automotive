"""Tests for binary_studio.identifiers — fixed-offset CALID/CVN reads into Findings."""

from __future__ import annotations

from samples import make_sample as ms
from src.binary_studio import identifiers
from src.core.findings import Severity, Source


def test_reads_calid_and_cvn_from_synthetic() -> None:
    buf = ms.build_sample()
    findings = identifiers.read_identifiers(buf, ms.LAYOUT)

    # Only the two identifier entries are surfaced (maps/checksum/patch_target are skipped).
    assert len(findings) == 2
    calid, cvn = findings
    assert calid.source is Source.BINARY and calid.severity is Severity.INFO
    assert "1267394012" in calid.title and "1267394012" in calid.detail
    assert "4A8B2C1E" in cvn.title
    assert cvn.raw == "4A8B2C1E"


def test_offset_accepts_int_and_hex_string() -> None:
    buf = ms.build_sample()
    as_str = identifiers.read_identifier(buf, "calid", {"offset": "0x20", "len": 10, "kind": "ascii"})
    as_int = identifiers.read_identifier(buf, "calid", {"offset": 0x20, "len": 10, "kind": "ascii"})
    assert as_str.title == as_int.title == "CALID: 1267394012"


def test_out_of_range_offset_is_failure() -> None:
    buf = bytes(64)
    f = identifiers.read_identifier(buf, "calid", {"offset": 0x1000, "len": 4, "kind": "ascii"})
    assert f.severity is Severity.FAIL
    assert "out of range" in f.title


def test_unsupported_kind_warns() -> None:
    buf = bytes(64)
    f = identifiers.read_identifier(buf, "cvn", {"offset": 0, "len": 4, "kind": "base64"})
    assert f.severity is Severity.WARN


def test_malformed_spec_warns() -> None:
    buf = bytes(64)
    f = identifiers.read_identifier(buf, "calid", {"offset": "0x0", "kind": "ascii"})  # no len
    assert f.severity is Severity.WARN


def test_ascii_null_padding_is_trimmed() -> None:
    buf = b"AB\x00\x00\x00\x00"
    f = identifiers.read_identifier(buf, "calid", {"offset": 0, "len": 6, "kind": "ascii"})
    assert f.title == "CALID: AB"
