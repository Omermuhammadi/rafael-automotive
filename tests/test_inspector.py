"""Tests for the OCPP deep-dive inspector."""

from __future__ import annotations

from src.core.findings import Finding, Severity, Source
from src.ocpp_triage import inspector, triage


def _finding_from(raw: str, direction: str, action: str | None = None) -> Finding:
    frame = triage.parse_frame(raw)
    f = triage.classify(direction, frame, action=action)
    f.raw = triage.pretty(raw)
    return f


def test_malformed_request_highlights_offending_field() -> None:
    raw = '[2,"1","StartTransaction",{"connectorId":1,"idTag":12345,"meterStart":0,"timestamp":"t"}]'
    insp = inspector.inspect(_finding_from(raw, triage.DIR_IN))
    assert insp.severity is Severity.FAIL
    assert '"idTag"' in insp.highlights
    assert any("idTag" in note for note in insp.notes)
    assert any("contactor" in note for note in insp.notes)


def test_rejected_result_highlights_status() -> None:
    raw = '[3,"1",{"idTagInfo":{"status":"Invalid"}}]'
    insp = inspector.inspect(_finding_from(raw, triage.DIR_OUT, action="Authorize"))
    assert '"status"' in insp.highlights and '"Invalid"' in insp.highlights
    assert any("broke the transaction" in note for note in insp.notes)


def test_accepted_result_has_no_highlights() -> None:
    raw = '[3,"1",{"idTagInfo":{"status":"Accepted"}}]'
    insp = inspector.inspect(_finding_from(raw, triage.DIR_OUT, action="Authorize"))
    assert insp.highlights == []
    assert any("Accepted" in note for note in insp.notes)


def test_callerror_notes_protocol_error() -> None:
    raw = '[4,"1","TypeConstraintViolation","bad type",{}]'
    insp = inspector.inspect(_finding_from(raw, triage.DIR_OUT, action="StartTransaction"))
    assert '"TypeConstraintViolation"' in insp.highlights
    assert any("Protocol error" in note for note in insp.notes)


def test_lifecycle_finding_has_nothing_to_inspect() -> None:
    f = Finding(Source.OCPP, Severity.INFO, "Charge point connected", "CP_1")  # raw is None
    insp = inspector.inspect(f)
    assert "nothing to inspect" in " ".join(insp.notes).lower()
