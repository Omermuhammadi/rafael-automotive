"""Pure logic for the hex viewer: turn a byte buffer into renderable rows.

No UI here (CLAUDE.md Section 7). The panel takes :class:`HexRow`s and paints them into a
monospace widget with an offset gutter. Keeping this headless means the exact bytes/offsets/
ASCII shown in the UI are asserted directly in tests, with no display required.

The viewer is *virtualised*: the panel asks for just the rows in view via :func:`rows`, so a
1 MiB image never has to be materialised into one giant string.
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_WIDTH = 16


@dataclass(frozen=True)
class HexRow:
    """One line of the hex dump: an offset and up to ``width`` bytes."""

    offset: int
    data: bytes

    def hex_cells(self, width: int = DEFAULT_WIDTH) -> str:
        """Space-separated two-digit hex, padded so short (final) rows keep column widths."""
        cells = [f"{b:02X}" for b in self.data]
        cells.extend("  " for _ in range(width - len(self.data)))
        # Group into two halves with a wider gap in the middle (classic hex-editor look).
        half = width // 2
        left = " ".join(cells[:half])
        right = " ".join(cells[half:])
        return f"{left}  {right}" if right else left

    def ascii_cells(self) -> str:
        """Printable ASCII (0x20-0x7E) for each byte, ``.`` otherwise."""
        return "".join(chr(b) if 0x20 <= b <= 0x7E else "." for b in self.data)

    def render(self, width: int = DEFAULT_WIDTH, offset_digits: int = 6) -> str:
        """Full line: ``offset  hex bytes  |ascii|``."""
        return f"{self.offset:0{offset_digits}X}  {self.hex_cells(width)}  |{self.ascii_cells()}|"


def total_rows(buf: bytes, width: int = DEFAULT_WIDTH) -> int:
    """Number of rows needed to show the whole buffer (last row may be partial)."""
    if width <= 0:
        raise ValueError("width must be positive")
    if not buf:
        return 0
    return (len(buf) + width - 1) // width


def offset_digits_for(buf: bytes) -> int:
    """Minimum even-ish hex-digit count for the largest offset (min 6, like 0x0FFFFF)."""
    highest = max(len(buf) - 1, 0)
    return max(6, len(f"{highest:X}"))


def rows(buf: bytes, start: int, count: int, width: int = DEFAULT_WIDTH) -> list[HexRow]:
    """Return up to ``count`` rows of ``width`` bytes, beginning at byte offset ``start``.

    ``start`` is a byte offset (the panel passes multiples of ``width`` so offsets stay
    aligned). Rows stop at the end of the buffer, so fewer than ``count`` may be returned and
    the final row may be short. ``start`` past the end yields an empty list.

    Raises:
        ValueError: if ``width`` or ``count`` is negative, or ``start`` is negative.
    """
    if width <= 0:
        raise ValueError("width must be positive")
    if count < 0:
        raise ValueError("count must be non-negative")
    if start < 0:
        raise ValueError("start must be non-negative")

    out: list[HexRow] = []
    offset = start
    end = len(buf)
    for _ in range(count):
        if offset >= end:
            break
        out.append(HexRow(offset=offset, data=bytes(buf[offset:offset + width])))
        offset += width
    return out
