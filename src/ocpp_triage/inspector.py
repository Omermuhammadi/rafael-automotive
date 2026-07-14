"""Deep-dive audit for a selected frame — the JSON browser behind a clicked log line.

Given a :class:`~src.core.findings.Finding` (whose ``raw`` holds the pretty-printed OCPP-J
frame), :func:`inspect` returns an :class:`Inspection`: the JSON to show, the substrings to
highlight, and plain-language notes explaining *why* it failed — e.g. an ``idTag`` sent as a
number is an "incorrect security token format", which is why the station's contactor stays open.

Pure logic, no UI: the panel renders the result.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.core.findings import Finding, Severity
from src.ocpp_triage import triage
from src.ocpp_triage.triage import MessageType

_FAIL_STATUSES = {"Rejected", "Blocked", "Expired", "Invalid", "ConcurrentTx", "Faulted"}


@dataclass
class Inspection:
    title: str
    severity: Severity
    pretty_json: str
    highlights: list[str] = field(default_factory=list)  # substrings to emphasise in the JSON
    notes: list[str] = field(default_factory=list)        # plain-language explanation


def _status_of(payload: object) -> str | None:
    if isinstance(payload, dict):
        info = payload.get("idTagInfo")
        if isinstance(info, dict):
            return info.get("status")
        return payload.get("status")
    return None


def _quoted_field(reason: str) -> str | None:
    match = re.search(r"'([^']+)'", reason)
    return match.group(1) if match else None


def inspect(finding: Finding) -> Inspection:
    """Analyse ``finding`` into a deep-dive view with highlights and explanatory notes."""
    raw = finding.raw
    if not raw:
        return Inspection(finding.title, finding.severity, "(no OCPP frame for this event)",
                          notes=["Lifecycle event — nothing to inspect."])

    try:
        frame = triage.parse_frame(raw)
    except ValueError:
        return Inspection(finding.title, finding.severity, raw,
                          notes=["Frame is not valid OCPP-J and could not be parsed."])

    highlights: list[str] = []
    notes: list[str] = []

    if frame.message_type == MessageType.CALL:
        problem = triage.validate_request(frame.action, frame.payload)
        if problem is not None:
            offending = _quoted_field(problem)
            if offending:
                highlights.append(f'"{offending}"')
            notes.append(f"Malformed {frame.action} request: {problem}.")
            notes.append("The central system cannot authorize this, so the contactor stays open.")
        else:
            notes.append(f"Well-formed {frame.action} request, awaiting a response.")

    elif frame.message_type == MessageType.CALLRESULT:
        status = _status_of(frame.payload)
        if status in _FAIL_STATUSES:
            highlights.extend(['"status"', f'"{status}"'])
            notes.append(f"Rejected outcome — status = {status}.")
            notes.append("This is the line that broke the transaction.")
        elif status:
            notes.append(f"Accepted — status = {status}.")
        else:
            notes.append("Response received.")

    else:  # CALLERROR
        if frame.error_code:
            highlights.append(f'"{frame.error_code}"')
        notes.append(f"Protocol error {frame.error_code}: {frame.error_description}.")

    return Inspection(finding.title, finding.severity, raw, highlights, notes)
