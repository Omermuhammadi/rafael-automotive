"""The OCPP fault-scenario library — "break things on purpose", the detail the client wants.

Eight scenarios, each a small async flow driven against the mock CSMS. They are the same
objects the UI drives (scenario dropdown) and the headless runner exercises:

    python -m src.ocpp_triage.scenarios --run all
    python -m src.ocpp_triage.scenarios --run invalid_auth
    python -m src.ocpp_triage.scenarios --list

Each scenario carries a :class:`~src.ocpp_triage.csms.CsmsPolicy` (how the server answers) and
a flow (how the charge point behaves). Findings stream to a log bus exactly as in the live UI.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import socket
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import websockets
from ocpp.v16 import call
from ocpp.v16.enums import ResetType

from src.core.findings import Finding, Severity, Source
from src.core.logbus import LogBus
from src.ocpp_triage.charge_point_sim import ChargePointSim, run_charge_session, spawn_background
from src.ocpp_triage.csms import DEFAULT_HOST, SUBPROTOCOL, CsmsPolicy, MockCsmsServer, utc_now


@dataclass
class ScenarioContext:
    """What a scenario flow needs: where the server is, the bus, and the server handle."""

    host: str
    port: int
    logbus: LogBus
    server: MockCsmsServer


@dataclass
class Scenario:
    id: str
    name: str
    description: str
    policy: CsmsPolicy
    flow: Callable[[ScenarioContext], Awaitable[None]]


# ── flows ─────────────────────────────────────────────────────────────────────

async def _happy(ctx: ScenarioContext) -> None:
    await run_charge_session(host=ctx.host, port=ctx.port, logbus=ctx.logbus, cp_id="CP_HAPPY")


async def _invalid_auth(ctx: ScenarioContext) -> None:
    await run_charge_session(host=ctx.host, port=ctx.port, logbus=ctx.logbus, cp_id="CP_AUTH")


async def _auth_timeout(ctx: ScenarioContext) -> None:
    # Server delays the auth response past the client's 1s timeout → the CP reports a timeout.
    await run_charge_session(host=ctx.host, port=ctx.port, logbus=ctx.logbus,
                             cp_id="CP_TIMEOUT", response_timeout=1.0)


async def _rejected_boot(ctx: ScenarioContext) -> None:
    await run_charge_session(host=ctx.host, port=ctx.port, logbus=ctx.logbus, cp_id="CP_BOOT")


async def _tx_invalid(ctx: ScenarioContext) -> None:
    await run_charge_session(host=ctx.host, port=ctx.port, logbus=ctx.logbus, cp_id="CP_TX")


async def _malformed(ctx: ScenarioContext) -> None:
    # A valid boot (green), then a StartTransaction whose idTag is a number, not a token string —
    # an "incorrect security token format". Sent as a raw frame to bypass client-side validation.
    uri = f"ws://{ctx.host}:{ctx.port}/CP_MALFORMED"
    async with websockets.connect(uri, subprotocols=[SUBPROTOCOL]) as ws:
        await ws.send(json.dumps([2, "b1", "BootNotification",
                                  {"chargePointModel": "MVDCT-SIM-1", "chargePointVendor": "MVDCT"}]))
        with contextlib.suppress(Exception):
            await asyncio.wait_for(ws.recv(), timeout=5)
        await ws.send(json.dumps([2, "s1", "StartTransaction",
                                  {"connectorId": 1, "idTag": 12345, "meterStart": 0,
                                   "timestamp": utc_now()}]))
        with contextlib.suppress(Exception):
            await asyncio.wait_for(ws.recv(), timeout=5)


async def _subprotocol_mismatch(ctx: ScenarioContext) -> None:
    uri = f"ws://{ctx.host}:{ctx.port}/CP_BADPROTO"
    try:
        async with websockets.connect(uri, subprotocols=["ocpp1.5"]):
            pass
    except Exception as exc:  # websockets refuses the handshake (HTTP 400)
        ctx.logbus.publish(Finding(
            Source.OCPP, Severity.FAIL, "CP → CSMS   subprotocol mismatch",
            f"Charge point requested ocpp1.5; server offers ocpp1.6 — connection refused "
            f"({type(exc).__name__}).",
        ))


async def _remote_reset(ctx: ScenarioContext) -> None:
    uri = f"ws://{ctx.host}:{ctx.port}/CP_RESET"
    async with websockets.connect(uri, subprotocols=[SUBPROTOCOL]) as ws:
        cp = ChargePointSim("CP_RESET", ws, logbus=ctx.logbus)
        receiver = spawn_background(cp.start())  # GC-safe strong ref (see charge_point_sim)
        try:
            await cp.call(call.BootNotification(charge_point_model="MVDCT-SIM-1",
                                                charge_point_vendor="MVDCT"))
            await asyncio.sleep(0.2)                      # a charge point is now connected
            await ctx.server.send_reset(reset_type=ResetType.hard)
            await asyncio.sleep(0.3)                      # let the reboot ack + finding flush
        finally:
            receiver.cancel()


SCENARIOS: list[Scenario] = [
    Scenario("happy", "Happy path",
             "Full session, everything accepted — all green.", CsmsPolicy(), _happy),
    Scenario("invalid_auth", "Invalid auth token",
             "Authorize returns Invalid → red; the CP never draws power.",
             CsmsPolicy(authorize_status="Invalid"), _invalid_auth),
    Scenario("auth_timeout", "Auth timeout",
             "Central system goes silent on Authorize → red timeout.",
             CsmsPolicy(response_delay=2.0, delay_action="Authorize"), _auth_timeout),
    Scenario("malformed", "Malformed payload",
             "StartTransaction with a bad idTag format → red; inspector shows the field.",
             CsmsPolicy(), _malformed),
    Scenario("rejected_boot", "Rejected boot",
             "BootNotification returns Rejected → red; session stops.",
             CsmsPolicy(boot_status="Rejected"), _rejected_boot),
    Scenario("tx_invalid", "Transaction invalid",
             "StartTransaction.conf Status: Invalid → red (the PDF's card-swipe freeze).",
             CsmsPolicy(start_status="Invalid"), _tx_invalid),
    Scenario("subprotocol", "Subprotocol mismatch",
             "Charge point speaks an unsupported OCPP subprotocol → connection refused.",
             CsmsPolicy(), _subprotocol_mismatch),
    Scenario("remote_reset", "Remote station reset",
             'Operator forces Reset {"type":"Hard"} → the station reboots.',
             CsmsPolicy(), _remote_reset),
]
SCENARIOS_BY_ID: dict[str, Scenario] = {s.id: s for s in SCENARIOS}
SCENARIOS_BY_NAME: dict[str, Scenario] = {s.name: s for s in SCENARIOS}


# ── running ───────────────────────────────────────────────────────────────────

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("localhost", 0))
        return s.getsockname()[1]


async def run_scenario_standalone(scenario: Scenario, logbus: LogBus | None = None,
                                  host: str = DEFAULT_HOST, port: int | None = None) -> LogBus:
    """Spin up a dedicated CSMS, run one scenario end to end, tear down. Returns the log bus."""
    bus = logbus or LogBus()
    port = port or _free_port()
    server = MockCsmsServer(bus, host=host, port=port, policy=scenario.policy)
    await server.start()
    ctx = ScenarioContext(host=host, port=port, logbus=bus, server=server)
    try:
        await scenario.flow(ctx)
        await asyncio.sleep(0.15)  # let trailing frames flush before teardown
    finally:
        await server.stop()
    return bus


# ── headless CLI ──────────────────────────────────────────────────────────────

_TAG = {Severity.OK: "[ OK ]", Severity.INFO: "[ .. ]", Severity.WARN: "[WARN]", Severity.FAIL: "[FAIL]"}


def _ascii(text: str) -> str:
    """Make text safe for any console (some Windows terminals are cp1252, not UTF-8)."""
    return text.replace("→", "->").replace("—", "-")


def _format(finding: Finding) -> str:
    line = f"  {_TAG[finding.severity]}  {finding.title}"
    if finding.detail and finding.severity in (Severity.FAIL, Severity.WARN):
        line += f"   - {finding.detail}"
    return _ascii(line)


async def _cli_run(which: str) -> None:
    scenarios = SCENARIOS if which == "all" else [SCENARIOS_BY_ID[which]]
    for scenario in scenarios:
        bus = LogBus()
        await run_scenario_standalone(scenario, logbus=bus)
        print(_ascii(f"\n=== [{scenario.id}] {scenario.name} - {scenario.description} ==="))
        for finding in bus.drain():
            if finding.source is Source.OCPP:
                print(_format(finding))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m src.ocpp_triage.scenarios",
        description="Run OCPP fault scenarios headlessly (no UI, no hardware).",
    )
    parser.add_argument("--run", default="all", metavar="ID", help="'all' (default) or a scenario id")
    parser.add_argument("--list", action="store_true", help="list scenario ids and exit")
    args = parser.parse_args(argv)

    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8")  # prefer UTF-8 where the console supports it

    if args.list:
        for scenario in SCENARIOS:
            print(f"{scenario.id:14} {scenario.name} — {scenario.description}")
        return
    if args.run != "all" and args.run not in SCENARIOS_BY_ID:
        parser.error(f"unknown scenario '{args.run}'. Use --list to see the options.")
    asyncio.run(_cli_run(args.run))


if __name__ == "__main__":
    main()
