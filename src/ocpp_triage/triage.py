"""Parse and classify OCPP-J frames into colour-coded Findings (pure logic, no I/O).

Every WebSocket message in OCPP-J is a JSON array:

    CALL        [2, "<uniqueId>", "<Action>", { ...payload }]
    CALLRESULT  [3, "<uniqueId>", { ...payload }]
    CALLERROR   [4, "<uniqueId>", "<errorCode>", "<errorDescription>", { ...details }]

:func:`parse_frame` turns a raw string into a :class:`Frame`; :func:`classify` turns a frame
(plus the resolved action and elapsed time from the sniffer's correlation table) into a
:class:`Finding` whose :class:`Severity` drives the row colour — green accepted, orange in
flight, red rejected/failed. This module is headless and fully unit-tested; the live sniffer
(``sniffer.py``) feeds it real frames off the wire.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import IntEnum

from src.core.findings import Finding, Severity, Source

# Direction labels (from the CSMS's vantage point — it is the "network authority").
DIR_IN = "CP → CSMS"   # inbound: a request from the charge point
DIR_OUT = "CSMS → CP"  # outbound: a response or command from the central system


class MessageType(IntEnum):
    CALL = 2
    CALLRESULT = 3
    CALLERROR = 4


@dataclass
class Frame:
    """A parsed OCPP-J message."""

    raw: str
    message_type: MessageType
    unique_id: str
    action: str | None                 # present on CALL; None on RESULT/ERROR
    payload: dict | list = field(default_factory=dict)
    error_code: str | None = None
    error_description: str | None = None


def parse_frame(raw: str) -> Frame:
    """Parse an OCPP-J frame string into a :class:`Frame`. Raises ``ValueError`` if malformed."""
    try:
        arr = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"not valid JSON: {exc}") from exc
    if not isinstance(arr, list) or len(arr) < 3:
        raise ValueError("not an OCPP-J frame (expected a JSON array of length >= 3)")

    type_id, unique_id = arr[0], str(arr[1])
    if type_id == MessageType.CALL:
        payload = arr[3] if len(arr) > 3 else {}
        return Frame(raw, MessageType.CALL, unique_id, str(arr[2]), payload)
    if type_id == MessageType.CALLRESULT:
        return Frame(raw, MessageType.CALLRESULT, unique_id, None, arr[2] if len(arr) > 2 else {})
    if type_id == MessageType.CALLERROR:
        return Frame(raw, MessageType.CALLERROR, unique_id, None,
                     arr[4] if len(arr) > 4 else {},
                     error_code=str(arr[2]) if len(arr) > 2 else None,
                     error_description=str(arr[3]) if len(arr) > 3 else None)
    raise ValueError(f"unknown OCPP message type id: {type_id!r}")


def pretty(raw: str) -> str:
    """Pretty-print a raw frame for the deep-dive inspector; return as-is if not JSON."""
    try:
        return json.dumps(json.loads(raw), indent=2)
    except (json.JSONDecodeError, TypeError):
        return raw


# Status values that read as a failure (red) vs. a soft/in-progress state (amber).
_FAIL_STATUSES = {"Rejected", "Blocked", "Expired", "Invalid", "ConcurrentTx", "Faulted"}
_WARN_STATUSES = {"Pending"}


def _result_status(payload: object) -> str | None:
    """Pull the outcome status from a CALLRESULT payload (idTagInfo.status or top-level status)."""
    if isinstance(payload, dict):
        info = payload.get("idTagInfo")
        if isinstance(info, dict) and "status" in info:
            return info["status"]
        if "status" in payload:
            return payload["status"]
    return None


def _summarize(action: str, payload: object) -> str:
    """A short human-readable summary of a request payload's salient fields."""
    if not isinstance(payload, dict) or not payload:
        return ""
    keys = ("chargePointModel", "chargePointVendor", "idTag", "connectorId",
            "transactionId", "meterStart", "meterStop", "type")
    bits = [f"{k}={payload[k]}" for k in keys if k in payload]
    return ", ".join(bits)


# Required fields (wire-name → type) for the request actions we care about. Used to catch
# malformed frames — e.g. an idTag sent as a number is an "incorrect security token format".
_REQUEST_SCHEMA: dict[str, dict[str, type]] = {
    "BootNotification": {"chargePointModel": str, "chargePointVendor": str},
    "Authorize": {"idTag": str},
    "StartTransaction": {"connectorId": int, "idTag": str, "meterStart": int, "timestamp": str},
    "StopTransaction": {"transactionId": int, "meterStop": int, "timestamp": str},
}


def validate_request(action: str, payload: object) -> str | None:
    """Return a human reason if a request payload is malformed, else None.

    Only the actions in ``_REQUEST_SCHEMA`` are checked; anything else is treated as fine.
    """
    schema = _REQUEST_SCHEMA.get(action)
    if schema is None:
        return None
    if not isinstance(payload, dict):
        return "payload is not a JSON object"
    for field_name, field_type in schema.items():
        if field_name not in payload:
            return f"missing required field '{field_name}'"
        value = payload[field_name]
        # bool is a subclass of int in Python; reject it explicitly for numeric fields.
        if field_type is int and isinstance(value, bool):
            return f"field '{field_name}' must be a number"
        if not isinstance(value, field_type):
            expected = "text" if field_type is str else "a number"
            return f"field '{field_name}' must be {expected}, got {type(value).__name__}"
    return None


def classify(direction: str, frame: Frame, action: str | None = None,
             elapsed: float | None = None) -> Finding:
    """Turn a frame into a Finding. ``action`` resolves CALLRESULT/ERROR (which omit it)."""
    act = action or frame.action or "?"

    if frame.message_type == MessageType.CALLERROR:
        return Finding(
            Source.OCPP, Severity.FAIL, f"{direction}   {act}.error",
            f"CALLERROR {frame.error_code}: {frame.error_description}",
        )

    if frame.message_type == MessageType.CALL:
        problem = validate_request(act, frame.payload)
        if problem is not None:
            return Finding(Source.OCPP, Severity.FAIL, f"{direction}   {act}.req   malformed",
                           problem)
        summary = _summarize(act, frame.payload)
        detail = f"Request in flight. {summary}".strip()
        return Finding(Source.OCPP, Severity.INFO, f"{direction}   {act}.req", detail)

    # CALLRESULT
    status = _result_status(frame.payload)
    if status in _FAIL_STATUSES:
        severity = Severity.FAIL
    elif status in _WARN_STATUSES:
        severity = Severity.WARN
    else:
        severity = Severity.OK

    title = f"{direction}   {act}.conf"
    if status:
        title += f"   {status}"
    detail_bits = []
    if status:
        detail_bits.append(f"status={status}")
    if elapsed is not None:
        detail_bits.append(f"{elapsed * 1000:.0f} ms")
    if severity is Severity.FAIL:
        detail_bits.append("→ transaction blocked")
    return Finding(Source.OCPP, severity, title, ", ".join(detail_bits) or "ok")
