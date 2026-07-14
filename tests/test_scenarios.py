"""Tests for the 8-scenario OCPP fault library (headless)."""

from __future__ import annotations

from src.core.findings import Severity, Source
from src.ocpp_triage import scenarios
from src.ocpp_triage.scenarios import SCENARIOS, SCENARIOS_BY_ID, run_scenario_standalone


async def _run(scenario_id: str):
    bus = await run_scenario_standalone(SCENARIOS_BY_ID[scenario_id])
    return [f for f in bus.drain() if f.source is Source.OCPP]


def _has_fail(findings, *needles: str) -> bool:
    return any(f.severity is Severity.FAIL and all(n in f.title for n in needles) for f in findings)


def test_library_has_eight_unique_scenarios() -> None:
    assert len(SCENARIOS) == 8
    assert len({s.id for s in SCENARIOS}) == 8


async def test_happy_path_has_no_failures() -> None:
    findings = await _run("happy")
    assert findings and all(f.severity is not Severity.FAIL for f in findings)


async def test_invalid_auth() -> None:
    assert _has_fail(await _run("invalid_auth"), "Authorize.conf", "Invalid")


async def test_auth_timeout() -> None:
    assert _has_fail(await _run("auth_timeout"), "timeout")


async def test_malformed_payload_flags_the_field() -> None:
    findings = await _run("malformed")
    bad = [f for f in findings if f.severity is Severity.FAIL and "malformed" in f.title]
    assert bad and "idTag" in bad[0].detail


async def test_rejected_boot() -> None:
    assert _has_fail(await _run("rejected_boot"), "BootNotification.conf", "Rejected")


async def test_transaction_invalid() -> None:
    assert _has_fail(await _run("tx_invalid"), "StartTransaction.conf", "Invalid")


async def test_subprotocol_mismatch() -> None:
    assert _has_fail(await _run("subprotocol"), "subprotocol")


async def test_remote_reset_flow() -> None:
    findings = await _run("remote_reset")
    titles = " | ".join(f.title for f in findings)
    assert "Reset.req" in titles
    assert any("Reset.conf" in f.title and f.severity is Severity.OK for f in findings)
    assert any("rebooting" in f.title for f in findings)


def test_cli_list(capsys) -> None:
    scenarios.main(["--list"])
    out = capsys.readouterr().out
    assert out.count("\n") >= 8 and "invalid_auth" in out
