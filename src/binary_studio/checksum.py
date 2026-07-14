"""Checksum validate + repair behind one interface (CRC16 / CRC32 / block-sum).

Modifying a byte in an ECU image invalidates its stored integrity checksum; flashing that
back can brick the unit. This module computes a checksum over a region and compares/repairs
the value stored elsewhere in the image.

Scope (SCOPE.md): three general algorithms ship behind one interface. A manufacturer-specific
scheme can be added as one more entry in ``_ALGO_FNS`` if a target ECU needs it — this is *not*
a universal manufacturer-checksum engine.

Algorithms:
    crc16    — CRC-16/XMODEM (poly 0x1021, init 0x0000; check "123456789" -> 0x31C3)
    crc32    — standard CRC-32 (zlib/PKZIP; check -> 0xCBF43926); matches samples/make_sample
    blocksum — arithmetic sum of word-sized little-endian chunks
"""

from __future__ import annotations

from collections.abc import Callable

from crccheck.crc import Crc16, Crc32

from src.core.findings import Finding, Severity, Source


def crc16(data: bytes) -> int:
    """CRC-16/XMODEM of ``data``."""
    return Crc16.calc(data)


def crc32(data: bytes) -> int:
    """Standard CRC-32 (zlib/PKZIP) of ``data``."""
    return Crc32.calc(data)


def block_sum(data: bytes, word: int = 1) -> int:
    """Arithmetic sum of ``data`` in ``word``-byte little-endian chunks (word=1 => sum of bytes)."""
    if word < 1:
        raise ValueError("word must be >= 1")
    if word == 1:
        return sum(data)
    total = 0
    for i in range(0, len(data), word):
        total += int.from_bytes(data[i:i + word], "little")
    return total


# algo name -> function over the region bytes
_ALGO_FNS: dict[str, Callable[[bytes], int]] = {
    "crc16": crc16,
    "crc32": crc32,
    "blocksum": block_sum,
}

SUPPORTED_ALGOS = tuple(_ALGO_FNS)


def _compute(buf: bytes, region: slice, algo: str, width: int) -> int:
    """Run ``algo`` over ``buf[region]`` and mask to the stored field ``width`` (bytes)."""
    try:
        fn = _ALGO_FNS[algo]
    except KeyError:
        raise ValueError(f"unknown algo '{algo}'; choose from {SUPPORTED_ALGOS}") from None
    value = fn(bytes(buf[region]))
    mask = (1 << (8 * width)) - 1
    return value & mask


def validate(buf: bytes, region: slice, stored: slice, algo: str, endian: str = "big") -> Finding:
    """Compare the freshly computed checksum over ``region`` to the value in ``stored``.

    Returns an OK Finding on a match, or a FAIL Finding (with expected vs. found) on mismatch.
    """
    width = stored.stop - stored.start
    computed = _compute(buf, region, algo, width)
    current = int.from_bytes(bytes(buf[stored]), endian)
    hexw = width * 2
    if computed == current:
        return Finding(
            Source.BINARY, Severity.OK, "Checksum valid",
            f"{algo.upper()} over {region.start:#x}..{region.stop:#x} = "
            f"0x{computed:0{hexw}X}, matches stored value.",
            raw=f"0x{computed:0{hexw}X}",
        )
    return Finding(
        Source.BINARY, Severity.FAIL, "Checksum mismatch",
        f"{algo.upper()} computed 0x{computed:0{hexw}X} but stored 0x{current:0{hexw}X} "
        f"@ {stored.start:#x}. File is unsafe to flash until repaired.",
        raw=f"computed=0x{computed:0{hexw}X} stored=0x{current:0{hexw}X}",
    )


def repair(buf: bytearray, region: slice, stored: slice, algo: str, endian: str = "big") -> Finding:
    """Recompute the checksum over ``region`` and write it into ``stored``. Returns an OK Finding."""
    width = stored.stop - stored.start
    computed = _compute(buf, region, algo, width)
    buf[stored] = computed.to_bytes(width, endian)
    hexw = width * 2
    return Finding(
        Source.BINARY, Severity.OK, "Checksum repaired",
        f"Wrote {algo.upper()} 0x{computed:0{hexw}X} to {stored.start:#x}..{stored.stop:#x}.",
        raw=f"0x{computed:0{hexw}X}",
    )
