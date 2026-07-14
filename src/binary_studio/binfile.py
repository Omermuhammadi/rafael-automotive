"""Load and save raw ECU images in .bin / .hex / .s19 (and friends).

`.bin` is read and written as raw bytes. Intel HEX (`.hex`) and Motorola S-record
(`.s19`/`.s28`/`.s37`/`.srec`) are ASCII container formats, so they go through ``bincopy``
(a proven, well-maintained library — CLAUDE.md Section 4 favours reuse over hand-rolling).

Everything downstream — hex view, identifiers, patching, checksum — works on a flat
``bytearray`` of the whole image, so this module's job is just: container format in/out.
"""

from __future__ import annotations

from pathlib import Path

import bincopy

# Suffixes we treat as ASCII container formats (parsed/emitted via bincopy).
_INTEL_HEX_SUFFIXES = {".hex", ".ihex"}
_SREC_SUFFIXES = {".s19", ".s28", ".s37", ".srec", ".srx", ".mot"}
CONTAINER_SUFFIXES = _INTEL_HEX_SUFFIXES | _SREC_SUFFIXES

# Suffixes (or anything unrecognised) read/written as raw bytes.
RAW_SUFFIXES = {".bin", ".rom", ".dump", ""}

SUPPORTED_SUFFIXES = CONTAINER_SUFFIXES | RAW_SUFFIXES


def load(path: str | Path) -> bytearray:
    """Load ``path`` into a flat ``bytearray`` of the whole image.

    ``.bin`` (and unrecognised suffixes) are read raw. ``.hex``/``.s19`` etc. are decoded
    via bincopy; the returned buffer spans from the record set's minimum address, with any
    internal gaps padded (0xFF) so offsets stay contiguous for the hex viewer.

    Raises:
        FileNotFoundError: if ``path`` does not exist.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"No such binary: {path}")

    suffix = path.suffix.lower()
    if suffix in _INTEL_HEX_SUFFIXES:
        bf = bincopy.BinFile()
        bf.add_ihex(path.read_text())
        return bytearray(bf.as_binary())
    if suffix in _SREC_SUFFIXES:
        bf = bincopy.BinFile()
        bf.add_srec(path.read_text())
        return bytearray(bf.as_binary())
    # Raw: .bin, .rom, unknown — a hex editor loads arbitrary bytes as-is.
    return bytearray(path.read_bytes())


def save(path: str | Path, buf: bytes) -> None:
    """Write ``buf`` to ``path``, choosing the container format from the suffix.

    ``.bin`` (and unrecognised suffixes) are written raw; ``.hex``/``.s19`` etc. are encoded
    via bincopy from base address 0.
    """
    path = Path(path)
    suffix = path.suffix.lower()

    if suffix in CONTAINER_SUFFIXES:
        bf = bincopy.BinFile()
        bf.add_binary(bytes(buf), address=0)
        text = bf.as_ihex() if suffix in _INTEL_HEX_SUFFIXES else bf.as_srec()
        path.write_text(text)
    else:
        path.write_bytes(bytes(buf))


def size(buf: bytes) -> int:
    """Length of the image in bytes."""
    return len(buf)
