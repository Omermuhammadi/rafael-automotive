"""Mock Central System (CSMS) — a local OCPP 1.6J server for triage without hardware.

``MockCsmsServer`` binds a WebSocket on ``ws://localhost:9000`` and hands each charge-point
connection to a :class:`CentralSystem` (an ocpp v16 ``ChargePoint`` playing the server role)
through a :class:`~src.ocpp_triage.sniffer.SniffingConnection`, so every frame is logged.

Handler behaviour is driven by :class:`CsmsPolicy`, so faults (invalid auth, rejected boot,
slow/timeout responses) can be injected deterministically. Phase 3 defaults to the happy path
plus an invalid-auth toggle; Phase 4 expands this into the full scenario library.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import websockets
from ocpp.routing import on
from ocpp.v16 import ChargePoint, call, call_result
from ocpp.v16.enums import Action, AuthorizationStatus, ResetType

from src.core.findings import Finding, Severity, Source
from src.core.logbus import LogBus
from src.ocpp_triage.sniffer import SniffingConnection

DEFAULT_HOST = "localhost"
DEFAULT_PORT = 9000
SUBPROTOCOL = "ocpp1.6"


def quiet_protocol_loggers() -> None:
    """Silence expected protocol errors (subprotocol negotiation, malformed frames).

    These are deliberately triggered by the fault scenarios and surfaced to the user as
    colour-coded Findings, so the library's raw tracebacks are noise, not signal.
    """
    for name in ("ocpp", "websockets.server"):
        logging.getLogger(name).setLevel(logging.CRITICAL)


def utc_now() -> str:
    """OCPP timestamps are ISO-8601 UTC."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class CsmsPolicy:
    """How the mock central system answers — tweak to inject faults."""

    boot_status: str = "Accepted"        # Accepted / Pending / Rejected
    authorize_status: str = "Accepted"   # Accepted / Blocked / Expired / Invalid
    start_status: str = "Accepted"       # Accepted / Invalid (matches the PDF's red case)
    boot_interval: int = 10
    response_delay: float = 0.0          # seconds; drives the timeout scenario
    delay_action: str | None = None      # if set, only this action is delayed (e.g. "Authorize")


class CentralSystem(ChargePoint):
    """Server-role OCPP 1.6 handler. Returns policy-driven responses to the charge point."""

    def __init__(self, id: str, connection, policy: CsmsPolicy | None = None, **kwargs) -> None:
        super().__init__(id, connection, **kwargs)
        self.policy = policy or CsmsPolicy()

    async def _maybe_delay(self, action: str) -> None:
        policy = self.policy
        if policy.response_delay and (policy.delay_action is None or policy.delay_action == action):
            await asyncio.sleep(policy.response_delay)

    @on(Action.boot_notification)
    async def on_boot(self, charge_point_model, charge_point_vendor, **kwargs):
        await self._maybe_delay("BootNotification")
        return call_result.BootNotification(
            current_time=utc_now(), interval=self.policy.boot_interval, status=self.policy.boot_status
        )

    @on(Action.authorize)
    async def on_authorize(self, id_tag, **kwargs):
        await self._maybe_delay("Authorize")
        return call_result.Authorize(id_tag_info={"status": self.policy.authorize_status})

    @on(Action.start_transaction)
    async def on_start_transaction(self, connector_id, id_tag, meter_start, timestamp, **kwargs):
        await self._maybe_delay("StartTransaction")
        return call_result.StartTransaction(
            transaction_id=1, id_tag_info={"status": self.policy.start_status}
        )

    @on(Action.heartbeat)
    async def on_heartbeat(self, **kwargs):
        return call_result.Heartbeat(current_time=utc_now())

    @on(Action.stop_transaction)
    async def on_stop_transaction(self, transaction_id, meter_stop, timestamp, **kwargs):
        return call_result.StopTransaction(id_tag_info={"status": AuthorizationStatus.accepted})

    @on(Action.status_notification)
    async def on_status_notification(self, **kwargs):
        return call_result.StatusNotification()


class MockCsmsServer:
    """Owns the WebSocket server lifecycle and publishes lifecycle events to the log bus."""

    def __init__(self, logbus: LogBus, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
                 policy: CsmsPolicy | None = None) -> None:
        self._logbus = logbus
        self.host = host
        self.port = port
        self.policy = policy or CsmsPolicy()
        self._server: websockets.Server | None = None
        self._active: dict[str, CentralSystem] = {}  # connected charge points, by id

    @property
    def running(self) -> bool:
        return self._server is not None

    @property
    def connected_ids(self) -> list[str]:
        return list(self._active)

    async def start(self) -> None:
        """Bind and start serving. Raises OSError if the port is already in use."""
        quiet_protocol_loggers()
        self._server = await websockets.serve(
            self._handle, self.host, self.port, subprotocols=[SUBPROTOCOL]
        )
        self._logbus.publish(Finding(
            Source.OCPP, Severity.OK, "CSMS listening",
            f"ws://{self.host}:{self.port}  (OCPP 1.6J)",
        ))

    async def _handle(self, connection) -> None:
        cp_id = connection.request.path.strip("/") or "CP"
        # Lock OCPP 1.6J: websockets rejects a *mismatched* subprotocol at the handshake; this
        # also refuses a client that negotiated *none* (Sec-WebSocket-Protocol must be ocpp1.6).
        if getattr(connection, "subprotocol", None) != SUBPROTOCOL:
            self._logbus.publish(Finding(
                Source.OCPP, Severity.FAIL, "Subprotocol not negotiated",
                f"Charge point did not agree on {SUBPROTOCOL}; refusing the connection.",
            ))
            await connection.close(code=1002, reason="ocpp1.6 subprotocol required")
            return
        self._logbus.publish(Finding(Source.OCPP, Severity.INFO, "Charge point connected", cp_id))
        cp = CentralSystem(cp_id, SniffingConnection(connection, self._logbus, cp_id),
                           policy=self.policy)
        self._active[cp_id] = cp
        try:
            await cp.start()
        except websockets.ConnectionClosed:
            pass
        finally:
            self._active.pop(cp_id, None)
            self._logbus.publish(Finding(
                Source.OCPP, Severity.INFO, "Charge point disconnected", cp_id
            ))

    async def send_reset(self, cp_id: str | None = None, reset_type: str = ResetType.hard) -> bool:
        """Issue a Reset command to a connected charge point (CSMS → CP). Returns True if sent.

        With ``cp_id`` None, resets the first connected charge point. The Reset.req/.conf frames
        are captured by the sniffer like any other, so they appear in the live log.
        """
        if not self._active:
            self._logbus.publish(Finding(
                Source.OCPP, Severity.WARN, "Remote reset skipped",
                "No charge point is currently connected.",
            ))
            return False
        target = self._active.get(cp_id) if cp_id else next(iter(self._active.values()))
        if target is None:
            return False
        await target.call(call.Reset(type=reset_type))
        return True

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
            self._active.clear()
            self._logbus.publish(Finding(Source.OCPP, Severity.INFO, "CSMS stopped", ""))
