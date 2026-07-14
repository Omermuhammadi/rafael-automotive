"""Read calibration identifiers (CALID / CVN) from an ECU image at *fixed offsets*.

This is the in-scope reading of the spec's "hunting matrix": it *scans fixed offsets* named
in an external layout (SCOPE.md) — it does not reverse-engineer maps out of unknown binaries
(that open-ended research problem is explicitly out of scope). The layout is config-driven so
the same function serves the synthetic fixture today and a real ECU's known offset table later.

    layout = {"calid": {"offset": "0x20", "len": 10, "kind": "ascii"},
              "cvn":   {"offset": "0x40", "len":  4, "kind": "hex"}}

Only entries carrying a recognised ``kind`` are treated as identifiers, so a richer layout
(maps, checksum spec, patch targets) can be passed in and the non-identifier entries ignored.
"""

from __future__ import annotations

from src.core.findings import Finding, Severity, Source

# Recognised value encodings.
_ASCII = "ascii"
_HEX = "hex"
SUPPORTED_KINDS = {_ASCII, _HEX}

# Friendly labels + a stable display order for known identifiers.
_LABELS = {"calid": "CALID", "cvn": "CVN"}
_ORDER = ["calid", "cvn"]


def _parse_offset(value: object) -> int:
    """Accept an int or a string like ``"0x20"`` / ``"32"``."""
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value, 0)
    raise TypeError(f"offset must be int or str, got {type(value).__name__}")


def _label(key: str) -> str:
    return _LABELS.get(key, key.upper())


def _sorted_identifier_keys(layout: dict) -> list[str]:
    """Known identifiers first (CALID, CVN), then any other ``kind``-bearing entries."""
    known = [k for k in _ORDER if k in layout]
    extra = sorted(k for k in layout if k not in _ORDER and isinstance(layout[k], dict)
                   and layout[k].get("kind") in SUPPORTED_KINDS)
    return known + extra


def read_identifier(buf: bytes, key: str, spec: dict) -> Finding:
    """Read a single identifier described by ``spec`` and return a :class:`Finding`."""
    label = _label(key)
    try:
        offset = _parse_offset(spec["offset"])
        length = int(spec["len"])
        kind = spec["kind"]
    except (KeyError, TypeError, ValueError) as exc:
        return Finding(Source.BINARY, Severity.WARN, f"{label}: bad layout",
                       f"Identifier spec for '{key}' is malformed: {exc}")

    if kind not in SUPPORTED_KINDS:
        return Finding(Source.BINARY, Severity.WARN, f"{label}: unsupported kind",
                       f"Don't know how to decode kind '{kind}'.")

    if offset < 0 or offset + length > len(buf):
        return Finding(Source.BINARY, Severity.FAIL, f"{label}: out of range",
                       f"Offset {offset:#x}+{length} exceeds image size {len(buf)} bytes.")

    raw = bytes(buf[offset:offset + length])
    if kind == _ASCII:
        text = raw.decode("ascii", errors="replace").rstrip("\x00")
        detail = f"{label}: {text}  ({length} bytes ASCII @ {offset:#07x})"
    else:  # hex
        text = raw.hex().upper()
        detail = f"{label}: {text}  ({length} bytes hex @ {offset:#07x})"

    return Finding(Source.BINARY, Severity.INFO, f"{label}: {text}", detail, raw=raw.hex().upper())


def read_identifiers(buf: bytes, layout: dict) -> list[Finding]:
    """Read every identifier entry in ``layout`` (fixed-offset scan) into Findings.

    Non-identifier layout entries (those without a recognised ``kind``) are skipped, so the
    full synthetic ``LAYOUT`` — which also describes maps and the checksum — can be passed in.
    """
    return [read_identifier(buf, key, layout[key]) for key in _sorted_identifier_keys(layout)]
