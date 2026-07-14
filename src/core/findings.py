"""The single reporting model every pillar emits.

A :class:`Finding` is a plain, UI-free record. Panels colour rows by :class:`Severity`
(the colour map lives in ``app/theme.py`` so this module stays free of UI imports — see
CLAUDE.md Section 7). Keeping one model across pillars means the hex studio, the OCPP
sniffer, and the hardware bridge all speak the same language to the log and to tests.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum


class Severity(Enum):
    """How a row reads at a glance. The UI maps each to a colour."""

    OK = "ok"      # green  — accepted / passed
    INFO = "info"  # orange — auth / transaction in flight
    WARN = "warn"  # amber  — non-fatal
    FAIL = "fail"  # red    — rejected / timeout / malformed / mismatch


class Source(Enum):
    """Which pillar produced the finding."""

    BINARY = "binary_studio"
    OCPP = "ocpp"
    HARDWARE = "hardware"


@dataclass
class Finding:
    """One reportable event.

    Attributes:
        source: The pillar that produced this.
        severity: Drives the row colour and the pass/fail read.
        title: Short label, e.g. ``"Checksum mismatch"``.
        detail: Plain-language what + why, for a technician.
        raw: Optional payload/bytes/frame for the deep-dive panel.
        ts: Creation time (epoch seconds); set automatically.
    """

    source: Source
    severity: Severity
    title: str
    detail: str
    raw: str | None = None
    ts: float = field(default_factory=time.time)

    @property
    def is_failure(self) -> bool:
        """True when this row should read as a failure (red)."""
        return self.severity is Severity.FAIL
