"""Tests for the synthetic ECU generator — the ground-truth fixture for later phases."""

from __future__ import annotations

from samples import make_sample as ms


def test_build_is_deterministic() -> None:
    assert ms.build_sample() == ms.build_sample()


def test_size_is_one_mib() -> None:
    assert len(ms.build_sample()) == ms.SIZE == 0x100000


def test_known_identifiers_at_known_offsets() -> None:
    data = ms.build_sample()
    calid = data[ms.CALID_OFFSET:ms.CALID_OFFSET + len(ms.CALID_TEXT)]
    assert calid.decode("ascii") == ms.CALID_TEXT == "1267394012"

    cvn = data[ms.CVN_OFFSET:ms.CVN_OFFSET + 4]
    assert cvn.hex().upper() == ms.CVN_HEX == "4A8B2C1E"


def test_known_map_regions() -> None:
    data = ms.build_sample()
    map2d = data[ms.MAP_2D_OFFSET:ms.MAP_2D_OFFSET + 256]
    assert list(map2d) == list(range(256))

    map3d = data[ms.MAP_3D_OFFSET:ms.MAP_3D_OFFSET + 512]
    assert list(map3d) == [i & 0xFF for i in range(512)]


def test_patch_target_region_holds_known_original() -> None:
    data = ms.build_sample()
    target = data[ms.PATCH_TARGET_OFFSET:ms.PATCH_TARGET_OFFSET + len(ms.PATCH_TARGET_BYTES)]
    assert target == ms.PATCH_TARGET_BYTES


def test_stored_checksum_is_valid_for_fresh_file() -> None:
    data = ms.build_sample()
    start, end = ms.CHECKSUM_REGION
    expected = ms.compute_crc32(data[start:end])
    stored = int.from_bytes(data[ms.CHECKSUM_STORED_OFFSET:ms.SIZE], "big")
    assert stored == expected


def test_write_sample_roundtrips_reproducibly(tmp_path) -> None:
    out = ms.write_sample(tmp_path / "synthetic_ecu.bin")
    assert out.read_bytes() == ms.build_sample()


def test_layout_offsets_match_constants() -> None:
    # The LAYOUT dict is the contract later phases import; keep it in sync with the bytes.
    assert ms.LAYOUT["calid"]["offset"] == hex(ms.CALID_OFFSET)
    assert ms.LAYOUT["cvn"]["offset"] == hex(ms.CVN_OFFSET)
    assert ms.LAYOUT["checksum"]["stored"] == hex(ms.CHECKSUM_STORED_OFFSET)
