"""Apply byte patches at defined offsets, loaded from an external patch-definition file.

This is the committed feature (CLAUDE.md Section 2): a general-purpose byte patcher. Patch
definitions live in the *user's* runtime file, never hardcoded here, so the repo stays a
neutral instrument. ``samples/example_patches.json`` is demo data whose offsets line up with
the synthetic fixture.

A patch may carry an ``original`` field — the bytes expected at the offset before writing. When
present, :func:`apply_patch` verifies it first, so applying a patch to the wrong file (or wrong
offset) fails loudly instead of silently corrupting the image.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from src.core.findings import Finding, Severity, Source


@dataclass(frozen=True)
class Patch:
    """One byte-level modification: write ``data`` at ``offset`` (optionally checking ``original``)."""

    id: str
    offset: int
    data: bytes
    original: bytes | None = None
    description: str = ""


def _parse_hex_bytes(text: str) -> bytes:
    """Parse ``"AA BB CC"`` / ``"AABBCC"`` / ``"0xAA,0xBB"`` into bytes."""
    cleaned = text.replace("0x", "").replace(",", " ").split()
    return bytes.fromhex("".join(cleaned)) if cleaned else b""


def _parse_offset(value: object) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value, 0)
    raise TypeError(f"offset must be int or str, got {type(value).__name__}")


def load_patch_defs(path: str | Path) -> list[Patch]:
    """Load patch definitions from a JSON file into :class:`Patch` objects.

    Expected shape::

        {"target": "...", "patches": [
            {"id": "...", "description": "...", "offset": "0x1A2F0",
             "original": "AA BB CC", "bytes": "00 00 00"}]}

    Raises:
        FileNotFoundError: if ``path`` is missing.
        ValueError: if the file is malformed, or a patch's ``original`` length differs from
            its ``bytes`` length (a definition should replace a region of equal size).
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"No such patch file: {path}")

    try:
        doc = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(f"Patch file is not valid JSON: {exc}") from exc

    entries = doc.get("patches")
    if not isinstance(entries, list):
        raise ValueError("Patch file must contain a 'patches' array.")

    patches: list[Patch] = []
    for i, entry in enumerate(entries):
        try:
            offset = _parse_offset(entry["offset"])
            data = _parse_hex_bytes(entry["bytes"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Patch #{i} is malformed: {exc}") from exc

        original = None
        if entry.get("original"):
            original = _parse_hex_bytes(entry["original"])
            if len(original) != len(data):
                raise ValueError(
                    f"Patch '{entry.get('id', i)}': original ({len(original)} bytes) and "
                    f"bytes ({len(data)} bytes) must be the same length."
                )

        patches.append(Patch(
            id=str(entry.get("id", f"patch_{i}")),
            offset=offset,
            data=data,
            original=original,
            description=str(entry.get("description", "")),
        ))
    return patches


def apply_patch(buf: bytearray, patch: Patch, verify_original: bool = True) -> Finding:
    """Write ``patch.data`` at ``patch.offset`` in ``buf`` (mutating it). Returns a Finding.

    If ``verify_original`` and ``patch.original`` is set, the current bytes must match first;
    otherwise a FAIL Finding is returned and ``buf`` is left untouched (wrong file / offset).
    """
    end = patch.offset + len(patch.data)
    if patch.offset < 0 or end > len(buf):
        return Finding(
            Source.BINARY, Severity.FAIL, f"Patch '{patch.id}': out of range",
            f"Offset {patch.offset:#x}+{len(patch.data)} exceeds image size {len(buf)} bytes.",
        )

    if verify_original and patch.original is not None:
        current = bytes(buf[patch.offset:patch.offset + len(patch.original)])
        if current != patch.original:
            return Finding(
                Source.BINARY, Severity.FAIL, f"Patch '{patch.id}': original mismatch",
                f"Expected {patch.original.hex(' ').upper()} at {patch.offset:#x} but found "
                f"{current.hex(' ').upper()}. Wrong file or offset — not applied.",
                raw=f"expected={patch.original.hex().upper()} found={current.hex().upper()}",
            )

    buf[patch.offset:end] = patch.data
    return Finding(
        Source.BINARY, Severity.OK, f"Patch '{patch.id}' applied",
        f"Wrote {len(patch.data)} bytes ({patch.data.hex(' ').upper()}) at {patch.offset:#07x}.",
        raw=patch.data.hex().upper(),
    )
