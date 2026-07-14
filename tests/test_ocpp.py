"""Tests for the OCPP pillar core: frame parsing/classification + live client↔server session."""

from __future__ import annotations

import socket

from src.core.findings import Severity, Source
from src.core.logbus import LogBus
from src.ocpp_triage import triage
from src.ocpp_triage.charge_point_sim import run_charge_session
from src.ocpp_triage.csms import CsmsPolicy, MockCsmsServer
from src.ocpp_triage.triage import Frame, MessageType

# ── pure: parsing ─────────────────────────────────────────────────────────────


def test_parse_call_result_and_error() -> None:
    call = triage.parse_frame('[2, "1", "BootNotification", {"chargePointVendor": "MVDCT"}]')
    assert call.message_type is MessageType.CALL
    assert call.action == "BootNotification" and call.payload["chargePointVendor"] == "MVDCT"

    result = triage.parse_frame('[3, "1", {"status": "Accepted"}]')
    assert result.message_type is MessageType.CALLRESULT and result.action is None

    err = triage.parse_frame('[4, "1", "FormationViolation", "bad", {}]')
    assert err.message_type is MessageType.CALLERROR and err.error_code == "FormationViolation"


def test_parse_malformed_raises() -> None:
    for bad in ["not json", "{}", "[2]", "[9, \"1\", \"x\"]"]:
        try:
            triage.parse_frame(bad)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {bad!r}")


# ── pure: classification / colour ─────────────────────────────────────────────


def test_classify_request_is_info() -> None:
    frame = triage.parse_frame('[2, "1", "Authorize", {"idTag": "TAG"}]')
    f = triage.classify(triage.DIR_IN, frame)
    assert f.severity is Severity.INFO and "Authorize.req" in f.title


def test_classify_accepted_is_ok_invalid_is_fail() -> None:
    ok = triage.parse_frame('[3, "1", {"idTagInfo": {"status": "Accepted"}}]')
    assert triage.classify(triage.DIR_OUT, ok, action="Authorize").severity is Severity.OK

    bad = triage.parse_frame('[3, "2", {"idTagInfo": {"status": "Invalid"}}]')
    red = triage.classify(triage.DIR_OUT, bad, action="Authorize")
    assert red.severity is Severity.FAIL and "Invalid" in red.title


def test_classify_rejected_boot_is_fail() -> None:
    frame = triage.parse_frame('[3, "1", {"status": "Rejected"}]')
    assert triage.classify(triage.DIR_OUT, frame, action="BootNotification").severity is Severity.FAIL


def test_pretty_prints_json() -> None:
    assert triage.pretty('[3,"1",{"status":"Accepted"}]').count("\n") > 0


# ── integration: live client ↔ server over websockets ─────────────────────────


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("localhost", 0))
        return s.getsockname()[1]


async def _run_session(policy: CsmsPolicy):
    bus = LogBus()
    port = _free_port()
    server = MockCsmsServer(bus, host="localhost", port=port, policy=policy)
    await server.start()
    try:
        result = await run_charge_session(host="localhost", port=port, heartbeats=2)
    finally:
        await server.stop()
    return result, [f for f in bus.drain() if f.source is Source.OCPP]


async def test_happy_path_streams_a_full_green_session() -> None:
    result, findings = await _run_session(CsmsPolicy())
    assert result is True
    assert all(f.severity is not Severity.FAIL for f in findings)
    titles = " | ".join(f.title for f in findings)
    for expected in ("BootNotification.req", "BootNotification.conf",
                     "Authorize.conf", "StartTransaction.conf", "StopTransaction.conf"):
        assert expected in titles, f"missing {expected} in {titles}"


async def test_injected_invalid_auth_turns_a_line_red() -> None:
    result, findings = await _run_session(CsmsPolicy(authorize_status="Invalid"))
    assert result is False  # the sim CP stops after an unauthorized token
    reds = [f for f in findings if f.severity is Severity.FAIL]
    assert any("Authorize.conf" in f.title and "Invalid" in f.title for f in reds), \
        [f.title for f in findings]


async def test_rejected_boot_stops_session() -> None:
    result, findings = await _run_session(CsmsPolicy(boot_status="Rejected"))
    assert result is False
    assert any(f.severity is Severity.FAIL and "BootNotification.conf" in f.title for f in findings)


async def test_csms_negotiates_and_locks_ocpp16() -> None:
    import websockets

    from src.ocpp_triage.csms import SUBPROTOCOL

    bus = LogBus()
    port = _free_port()
    server = MockCsmsServer(bus, host="localhost", port=port)
    await server.start()
    try:
        async with websockets.connect(f"ws://localhost:{port}/CP", subprotocols=["ocpp1.6"]) as ws:
            assert ws.subprotocol == SUBPROTOCOL == "ocpp1.6"  # Sec-WebSocket-Protocol negotiated
    finally:
        await server.stop()
