"""Simulated charge point (client) — drives a session so we can test with zero hardware.

``ChargePointSim`` connects to the mock CSMS and runs a realistic session:
Boot → Authorize → StartTransaction → Heartbeat×N → StopTransaction. It behaves like a real
station: if boot is rejected or the token is not authorized, it stops early (the contactor
stays open), and if the central system goes silent it reports a timeout — both of which the
sniffer highlights. It also answers a remote ``Reset`` (CSMS → CP) by "rebooting".
"""

from __future__ import annotations

import asyncio

import websockets
from ocpp.routing import on
from ocpp.v16 import ChargePoint, call, call_result
from ocpp.v16.enums import Action, ResetStatus

from src.core.findings import Finding, Severity, Source
from src.core.logbus import LogBus
from src.ocpp_triage.csms import DEFAULT_HOST, DEFAULT_PORT, SUBPROTOCOL, utc_now

DEFAULT_ID_TAG = "MVDCT-TAG-01"
DEFAULT_CP_ID = "CP_1"

# The event loop keeps only a *weak* reference to a task, so a fire-and-forget task can be
# garbage-collected mid-flight (CPython asyncio docs). Track background tasks in a set — and
# discard on completion — so they always survive until they finish.
_background_tasks: set[asyncio.Task] = set()


def spawn_background(coro) -> asyncio.Task:
    """Schedule ``coro`` as a background task, holding a strong reference until it completes."""
    task = asyncio.ensure_future(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


class ChargePointSim(ChargePoint):
    """Client-role OCPP 1.6 charge point that plays out a charging session."""

    def __init__(self, id: str, connection, logbus: LogBus | None = None,
                 response_timeout: float = 30, **kwargs) -> None:
        super().__init__(id, connection, response_timeout=response_timeout, **kwargs)
        self._logbus = logbus
        self._response_timeout = response_timeout
        self.rebooted = False

    @on(Action.reset)
    async def on_reset(self, type, **kwargs):
        """Answer a remote Reset (CSMS → CP) and 'reboot' the station."""
        self.rebooted = True
        if self._logbus is not None:
            self._logbus.publish(Finding(
                Source.OCPP, Severity.WARN, "Charge point rebooting",
                f"Reset ({type}) accepted — controller performing a full reboot.",
            ))
        return call_result.Reset(status=ResetStatus.accepted)

    async def run_session(self, id_tag: str = DEFAULT_ID_TAG, heartbeats: int = 3) -> bool:
        """Run Boot→Authorize→Start→Heartbeat×N→Stop. Returns True if a transaction completed."""
        try:
            boot = await self.call(call.BootNotification(
                charge_point_model="MVDCT-SIM-1", charge_point_vendor="MVDCT"
            ))
            if getattr(boot, "status", None) != "Accepted":
                return False  # central system rejected the boot; a real CP would not proceed

            authorize = await self.call(call.Authorize(id_tag=id_tag))
            if _status(authorize) != "Accepted":
                return False  # token not authorized — stop before drawing power

            start = await self.call(call.StartTransaction(
                connector_id=1, id_tag=id_tag, meter_start=0, timestamp=utc_now()
            ))
            if _status(start) != "Accepted":
                return False  # transaction refused (e.g. Status: Invalid)

            transaction_id = getattr(start, "transaction_id", 1)
            meter = 0
            for _ in range(heartbeats):
                await self.call(call.Heartbeat())
                meter += 500

            await self.call(call.StopTransaction(
                transaction_id=transaction_id, meter_stop=meter, timestamp=utc_now()
            ))
            return True
        except asyncio.TimeoutError:
            if self._logbus is not None:
                self._logbus.publish(Finding(
                    Source.OCPP, Severity.FAIL, "CP → CSMS   request timeout",
                    f"No response within {self._response_timeout:g}s — session dropped.",
                ))
            return False


def _status(result) -> str | None:
    info = getattr(result, "id_tag_info", None)
    return info.get("status") if isinstance(info, dict) else None


async def run_charge_session(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
                             id_tag: str = DEFAULT_ID_TAG, heartbeats: int = 3,
                             cp_id: str = DEFAULT_CP_ID, logbus: LogBus | None = None,
                             response_timeout: float = 30) -> bool:
    """Connect to the CSMS, run one session, and disconnect cleanly. Returns the session result."""
    uri = f"ws://{host}:{port}/{cp_id}"
    async with websockets.connect(uri, subprotocols=[SUBPROTOCOL]) as ws:
        cp = ChargePointSim(cp_id, ws, logbus=logbus, response_timeout=response_timeout)
        receiver = spawn_background(cp.start())  # background recv loop (GC-safe strong ref)
        try:
            return await cp.run_session(id_tag=id_tag, heartbeats=heartbeats)
        finally:
            receiver.cancel()
