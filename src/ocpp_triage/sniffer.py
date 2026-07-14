"""A transparent tap that turns a live OCPP connection into a stream of Findings.

:class:`SniffingConnection` wraps the WebSocket connection the ocpp library reads/writes
(it only needs ``recv``/``send``), publishing a :class:`Finding` for every raw frame to the
log bus. It keeps a small correlation table so a CALLRESULT — which carries no action name —
is matched back to its originating CALL for the action label and round-trip time.

Placed on the CSMS side, this captures the whole conversation from the central system's
vantage point (inbound requests + outbound responses/commands), which is exactly the sniffer
the client asked for.
"""

from __future__ import annotations

import time
from typing import Any

from src.core.findings import Finding, Severity, Source
from src.core.logbus import LogBus
from src.ocpp_triage import triage
from src.ocpp_triage.triage import MessageType


class SniffingConnection:
    """Wrap an ocpp WebSocket connection, publishing a Finding per frame to ``logbus``."""

    def __init__(self, connection: Any, logbus: LogBus, label: str = "CP") -> None:
        self._conn = connection
        self._logbus = logbus
        self._label = label
        # unique_id -> (action, monotonic send/recv time) for request/response correlation
        self._pending: dict[str, tuple[str | None, float]] = {}

    def __getattr__(self, name: str) -> Any:
        # Delegate anything the ocpp lib might touch (close, etc.) to the real connection.
        return getattr(self._conn, name)

    async def recv(self) -> str:
        message = await self._conn.recv()
        self._observe(message, triage.DIR_IN)
        return message

    async def send(self, message: str) -> None:
        self._observe(message, triage.DIR_OUT)
        await self._conn.send(message)

    def _observe(self, raw: Any, direction: str) -> None:
        text = raw if isinstance(raw, str) else str(raw)
        try:
            frame = triage.parse_frame(text)
        except ValueError as exc:
            self._logbus.publish(Finding(
                Source.OCPP, Severity.FAIL, f"{direction}   malformed frame",
                str(exc), raw=triage.pretty(text),
            ))
            return

        action = frame.action
        elapsed: float | None = None
        if frame.message_type == MessageType.CALL:
            self._pending[frame.unique_id] = (action, time.monotonic())
        else:
            action, sent_at = self._pending.pop(frame.unique_id, (None, None))
            if sent_at is not None:
                elapsed = time.monotonic() - sent_at

        finding = triage.classify(direction, frame, action=action, elapsed=elapsed)
        finding.raw = triage.pretty(text)
        self._logbus.publish(finding)
