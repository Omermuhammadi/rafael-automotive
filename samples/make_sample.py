"""Generate the ground-truth synthetic ECU binary.

Why this exists (CLAUDE.md Section 5): no real dump is provided or needed. We synthesise a
controlled 1 MiB image with **known** values at **known** offsets so every later phase can
be tested against trustworthy ground truth — hex view, CALID/CVN read, patch-at-offset, and
checksum validate/repair.

The image is fully deterministic: it is a pure function of the constants below, with no RNG
and no clock, so ``build_sample()`` returns identical bytes on every run and on every machine
(the background fill uses integer arithmetic only, not a version-dependent RNG stream).

Layout (offsets are absolute in the 1 MiB image)::

    0x00000  header tag         b"MVDCT-SYNTH-ECU\\0"     (16 bytes)
    0x00020  CALID              ASCII "1267394012"        (10 bytes)
    0x00040  CVN                hex   4A 8B 2C 1E         ( 4 bytes)
    0x01000  2D map             16x16 uint8 ramp          (256 bytes)
    0x02000  3D map             8x8x8 uint8 ramp          (512 bytes)
    0x1A2F0  patch target       AA BB CC DD EE FF 00 11   ( 8 bytes)
    0xFFFFC  stored checksum    CRC32 of [0, 0xFFFFC), big-endian (4 bytes)

The CVN is stored as raw bytes and read back as hex; the example CRC value the client spec
prints (``0x9F2E4A1B``) is illustrative — the real stored CRC is whatever the synthetic
region hashes to, and the file is self-consistent (freshly built => checksum VALID).

Run directly to (re)write ``samples/synthetic_ecu.bin``::

    python -m samples.make_sample
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from crccheck.crc import Crc32

# ── Geometry ────────────────────────────────────────────────────────────────
SIZE = 0x100000  # 1 MiB flash, addresses 0x00000..0xFFFFF (matches the spec's 1MB chip)

HEADER_TAG = b"MVDCT-SYNTH-ECU\x00"  # 16 bytes

CALID_TEXT = "1267394012"  # client-spec sample CALID
CVN_HEX = "4A8B2C1E"       # client-spec sample CVN

CALID_OFFSET = 0x00020
CVN_OFFSET = 0x00040
MAP_2D_OFFSET = 0x01000
MAP_3D_OFFSET = 0x02000
PATCH_TARGET_OFFSET = 0x1A2F0
PATCH_TARGET_BYTES = bytes.fromhex("AABBCCDDEEFF0011")  # known original for Phase 2 patches

CHECKSUM_ALGO = "crc32"
CHECKSUM_STORED_OFFSET = SIZE - 4          # last 4 bytes hold the stored checksum
CHECKSUM_REGION = (0, SIZE - 4)            # region the checksum covers (start, end-exclusive)

# ── Ground-truth layout other phases consume ────────────────────────────────
# `calid`/`cvn` use the exact shape BUILD_PLAN's read_identifiers() expects.
LAYOUT: dict[str, dict] = {
    "calid": {"offset": hex(CALID_OFFSET), "len": len(CALID_TEXT), "kind": "ascii"},
    "cvn": {"offset": hex(CVN_OFFSET), "len": len(CVN_HEX) // 2, "kind": "hex"},
    "map_2d": {"offset": hex(MAP_2D_OFFSET), "shape": [16, 16], "dtype": "uint8"},
    "map_3d": {"offset": hex(MAP_3D_OFFSET), "shape": [8, 8, 8], "dtype": "uint8"},
    "patch_target": {"offset": hex(PATCH_TARGET_OFFSET), "len": len(PATCH_TARGET_BYTES)},
    "checksum": {
        "algo": CHECKSUM_ALGO,
        "region": [hex(CHECKSUM_REGION[0]), hex(CHECKSUM_REGION[1])],
        "stored": hex(CHECKSUM_STORED_OFFSET),
        "endian": "big",
        "width": 4,
    },
}

DEFAULT_PATH = Path(__file__).with_name("synthetic_ecu.bin")


def _background(size: int) -> bytearray:
    """Deterministic, non-trivial fill (Knuth multiplicative hash of the byte index).

    Integer arithmetic only, so the bytes are identical across machines and library
    versions. uint64 keeps the multiply well within range (no overflow, no warnings).
    """
    idx = np.arange(size, dtype=np.uint64)
    fill = ((idx * np.uint64(2654435761)) >> np.uint64(16)) & np.uint64(0xFF)
    return bytearray(fill.astype(np.uint8).tobytes())


def _map_2d() -> bytes:
    """16x16 uint8 ramp (values 0..255) — a known 2D calibration table."""
    return np.arange(256, dtype=np.uint8).reshape(16, 16).tobytes()


def _map_3d() -> bytes:
    """8x8x8 uint8 ramp (values 0..255, wrapping) — a known 3D calibration table."""
    return (np.arange(512, dtype=np.uint16) & 0xFF).astype(np.uint8).reshape(8, 8, 8).tobytes()


def compute_crc32(data: bytes) -> int:
    """Standard CRC-32 (zlib/PKZIP) over ``data`` — the algorithm checksum.py will reuse."""
    return Crc32.calc(data)


def build_sample() -> bytes:
    """Return the deterministic 1 MiB image with a valid stored checksum."""
    buf = _background(SIZE)

    buf[0x00000:0x00000 + len(HEADER_TAG)] = HEADER_TAG
    buf[CALID_OFFSET:CALID_OFFSET + len(CALID_TEXT)] = CALID_TEXT.encode("ascii")
    buf[CVN_OFFSET:CVN_OFFSET + 4] = bytes.fromhex(CVN_HEX)
    buf[MAP_2D_OFFSET:MAP_2D_OFFSET + 256] = _map_2d()
    buf[MAP_3D_OFFSET:MAP_3D_OFFSET + 512] = _map_3d()
    buf[PATCH_TARGET_OFFSET:PATCH_TARGET_OFFSET + len(PATCH_TARGET_BYTES)] = PATCH_TARGET_BYTES

    # Compute the checksum over its region and store it last, so the fresh file is VALID.
    crc = compute_crc32(bytes(buf[CHECKSUM_REGION[0]:CHECKSUM_REGION[1]]))
    buf[CHECKSUM_STORED_OFFSET:SIZE] = crc.to_bytes(4, "big")

    return bytes(buf)


def write_sample(path: Path | str = DEFAULT_PATH) -> Path:
    """Write the synthetic image to ``path`` and return the path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(build_sample())
    return path


def main() -> None:
    data = build_sample()
    path = write_sample()
    crc = compute_crc32(data[CHECKSUM_REGION[0]:CHECKSUM_REGION[1]])
    print(f"Wrote {path}  ({len(data):,} bytes)")
    print(f"  CALID @ {hex(CALID_OFFSET)} : {CALID_TEXT}")
    print(f"  CVN   @ {hex(CVN_OFFSET)} : {CVN_HEX}")
    print(f"  CRC32 @ {hex(CHECKSUM_STORED_OFFSET)} : 0x{crc:08X} (stored big-endian)")


if __name__ == "__main__":
    main()
