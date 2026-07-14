"""UDS (ISO 14229) read/write sequence behind a swappable ``Transport`` seam.

This is the "extract off / flash to the vehicle" logic from the client's Pillar A workflow. It
is written here and proven against a **software mock ECU** (``mock_ecu.py``); the real Tactrix
J2534 cable plugs into the same :class:`Transport` seam and is validated by the client on the
vehicle (CLAUDE.md Section 3). Nothing here claims the real path is verified.

Design note: rather than pull in a full UDS stack (udsoncan + isotp + python-can), the services
are hand-rolled at the application-payload level so the tests can assert the **exact** byte
sequence the spec calls for, with the mock ECU as the reference implementation. The transport
carries whole UDS messages (post-ISO-TP): the J2534 device handles CAN segmentation for the
real path; the mock answers request/response directly.

Sequence (from the client spec v2):
    extract : 0x10 0x03 → 0x27 seed/key → 0x35 → 0x36 loop → 0x37
    write   : 0x10 0x02 → 0x27 seed/key → 0x34 → 0x36 loop → 0x37 → 0x11 0x01
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, runtime_checkable

from src.core.findings import Finding, Severity, Source

# ── UDS service ids (request) and their positive-response offset ───────────────
SID_DIAGNOSTIC_SESSION_CONTROL = 0x10
SID_ECU_RESET = 0x11
SID_SECURITY_ACCESS = 0x27
SID_REQUEST_DOWNLOAD = 0x34
SID_REQUEST_UPLOAD = 0x35
SID_TRANSFER_DATA = 0x36
SID_REQUEST_TRANSFER_EXIT = 0x37

POSITIVE_RESPONSE_OFFSET = 0x40
NEGATIVE_RESPONSE = 0x7F

# Session types (0x10)
SESSION_DEFAULT = 0x01
SESSION_PROGRAMMING = 0x02
SESSION_EXTENDED = 0x03

# Security access sub-functions (0x27)
SECURITY_REQUEST_SEED = 0x01
SECURITY_SEND_KEY = 0x02

# ECU reset types (0x11)
RESET_HARD = 0x01

# addressAndLengthFormatIdentifier 0x44 = 4-byte address + 4-byte size
ADDR_LEN_FORMAT_4_4 = 0x44
DATA_FORMAT_NONE = 0x00

# requestCorrectlyReceived-ResponsePending: the ECU needs more time (common during flash
# erase/program). The client must keep waiting for the real reply, not treat it as a failure.
NRC_RESPONSE_PENDING = 0x78
_MAX_PENDING = 30  # give up after this many consecutive 0x78s so we can never hang

# ISO-TP (ISO 15765-2) classic addressing caps one segmented message at 4095 bytes, so every
# UDS message (incl. SID+seq) must stay within it. On the real cable the J2534 device performs
# the ISO-TP fragmentation/reassembly (protocol ISO15765); we just keep each block in range.
ISOTP_MAX_MESSAGE = 4095

# Negative response codes we surface by name
_NRC_NAMES = {
    0x11: "serviceNotSupported",
    0x12: "subFunctionNotSupported",
    0x22: "conditionsNotCorrect",
    0x31: "requestOutOfRange",
    0x33: "securityAccessDenied",
    0x35: "invalidKey",
    0x73: "wrongBlockSequenceCounter",
    0x78: "responsePending",
}

SeedKeyFn = Callable[[bytes], bytes]


@runtime_checkable
class Transport(Protocol):
    """Carries whole UDS messages. The mock and the J2534 cable both implement this."""

    def send(self, data: bytes) -> None: ...
    def recv(self, timeout: float = 2.0) -> bytes: ...


class UdsError(Exception):
    """A negative, empty, or unexpected UDS response."""


def toy_seed_key(seed: bytes) -> bytes:
    """A deliberately simple seed→key transform for the mock ECU.

    The *real* per-ECU algorithm is the client's to supply — it plugs in via ``seed_key_fn``.
    This toy version (XOR 0x5A) exists only so the mock's handshake round-trips in software.
    """
    return bytes(b ^ 0x5A for b in seed)


def _nrc_name(nrc: int) -> str:
    return _NRC_NAMES.get(nrc, f"0x{nrc:02X}")


def _request(transport: Transport, payload: bytes, timeout: float = 2.0) -> bytes:
    """Send a UDS request and return the validated positive response bytes.

    Handles ``responsePending`` (NRC 0x78): the ECU may reply ``7F <sid> 78`` repeatedly while
    it works; we keep re-reading (without resending) until the real reply or a bounded retry
    limit, so a busy ECU never hangs or crashes the client.
    """
    transport.send(payload)
    for _ in range(_MAX_PENDING + 1):
        response = transport.recv(timeout)
        if not response:
            raise UdsError(f"no response to service 0x{payload[0]:02X} (timeout)")
        if response[0] == NEGATIVE_RESPONSE:
            nrc = response[2] if len(response) > 2 else 0
            if nrc == NRC_RESPONSE_PENDING:
                continue  # ECU asked for more time; keep waiting for the real reply
            raise UdsError(f"service 0x{payload[0]:02X} rejected: {_nrc_name(nrc)}")
        if response[0] != payload[0] + POSITIVE_RESPONSE_OFFSET:
            raise UdsError(f"unexpected response 0x{response[0]:02X} to service 0x{payload[0]:02X}")
        return response
    raise UdsError(f"service 0x{payload[0]:02X}: too many response-pending (0x78) replies")


def _data_per_block(resp: bytes) -> int:
    """Bytes of payload per TransferData block, capped to stay within one ISO-TP message."""
    advertised = _max_block_length(resp) - 2  # minus the SID + seq overhead of 0x36
    return max(1, min(advertised, ISOTP_MAX_MESSAGE - 2))


def _block_seq(index: int) -> int:
    """UDS blockSequenceCounter: 0x01, 0x02, … 0xFF, 0x00, 0x01 … (index is 1-based)."""
    return index & 0xFF


# ── low-level services (the BUILD_PLAN contract) ──────────────────────────────

def start_session(transport: Transport, session: int = SESSION_EXTENDED) -> Finding:
    """0x10 — DiagnosticSessionControl."""
    try:
        _request(transport, bytes([SID_DIAGNOSTIC_SESSION_CONTROL, session]))
    except UdsError as exc:
        return Finding(Source.HARDWARE, Severity.FAIL, "Session control failed", str(exc))
    name = {SESSION_PROGRAMMING: "programming", SESSION_EXTENDED: "extended"}.get(session, "custom")
    return Finding(Source.HARDWARE, Severity.OK, "Diagnostic session started",
                   f"{name} session active (0x10 0x{session:02X}).")


def security_access(transport: Transport, seed_key_fn: SeedKeyFn = toy_seed_key) -> Finding:
    """0x27 — SecurityAccess seed/key handshake."""
    try:
        seed_resp = _request(transport, bytes([SID_SECURITY_ACCESS, SECURITY_REQUEST_SEED]))
        seed = bytes(seed_resp[2:])
        if not seed or all(b == 0 for b in seed):
            return Finding(Source.HARDWARE, Severity.OK, "Security access already unlocked",
                           "ECU returned a zero seed.")
        key = seed_key_fn(seed)
        _request(transport, bytes([SID_SECURITY_ACCESS, SECURITY_SEND_KEY]) + key)
    except UdsError as exc:
        return Finding(Source.HARDWARE, Severity.FAIL, "Security access denied", str(exc))
    return Finding(Source.HARDWARE, Severity.OK, "Security access granted",
                   "Seed/key handshake complete (0x27 0x01 → 0x27 0x02).")


def request_upload(transport: Transport, start: int, size: int,
                   progress: Callable[[int, int], None] | None = None) -> bytes:
    """0x35 then a 0x36 loop — read ``size`` bytes from the ECU starting at ``start``.

    Returns the reassembled image. Raises :class:`UdsError` on any failure.
    """
    req = (bytes([SID_REQUEST_UPLOAD, DATA_FORMAT_NONE, ADDR_LEN_FORMAT_4_4])
           + start.to_bytes(4, "big") + size.to_bytes(4, "big"))
    resp = _request(transport, req)
    data_per_block = _data_per_block(resp)

    data = bytearray()
    index = 1
    while len(data) < size:
        block = _request(transport, bytes([SID_TRANSFER_DATA, _block_seq(index)]))
        if len(block) >= 2 and block[1] != _block_seq(index):
            raise UdsError(f"block sequence mismatch at block {index}")
        data.extend(block[2:])
        index += 1
        if progress is not None:
            progress(min(len(data), size), size)
    _request(transport, bytes([SID_REQUEST_TRANSFER_EXIT]))
    return bytes(data[:size])


def request_download(transport: Transport, start: int, data: bytes,
                     progress: Callable[[int, int], None] | None = None) -> Finding:
    """0x34 then a 0x36 loop — write ``data`` to the ECU starting at ``start``."""
    size = len(data)
    req = (bytes([SID_REQUEST_DOWNLOAD, DATA_FORMAT_NONE, ADDR_LEN_FORMAT_4_4])
           + start.to_bytes(4, "big") + size.to_bytes(4, "big"))
    try:
        resp = _request(transport, req)
        data_per_block = _data_per_block(resp)
        index = 1
        for offset in range(0, size, data_per_block):
            chunk = data[offset:offset + data_per_block]
            _request(transport, bytes([SID_TRANSFER_DATA, _block_seq(index)]) + chunk)
            index += 1
            if progress is not None:
                progress(min(offset + len(chunk), size), size)
        _request(transport, bytes([SID_REQUEST_TRANSFER_EXIT]))
    except UdsError as exc:
        return Finding(Source.HARDWARE, Severity.FAIL, "Write-back failed", str(exc))
    return Finding(Source.HARDWARE, Severity.OK, "Write-back complete",
                   f"Downloaded {size:,} bytes to {start:#08x} (0x34/0x36).")


def ecu_reset(transport: Transport, reset_type: int = RESET_HARD) -> Finding:
    """0x11 — ECUReset."""
    try:
        _request(transport, bytes([SID_ECU_RESET, reset_type]))
    except UdsError as exc:
        return Finding(Source.HARDWARE, Severity.FAIL, "ECU reset failed", str(exc))
    return Finding(Source.HARDWARE, Severity.OK, "ECU reset",
                   f"ECU rebooted (0x11 0x{reset_type:02X}).")


def _max_block_length(resp: bytes) -> int:
    """Parse maxNumberOfBlockLength from a 0x74/0x75 positive response; default if absent."""
    if len(resp) >= 2:
        n = resp[1] >> 4
        if n and len(resp) >= 2 + n:
            return int.from_bytes(resp[2:2 + n], "big")
    return 4096


# ── high-level flows (used by the UI, publish Findings) ───────────────────────

def initialize(transport: Transport, seed_key_fn: SeedKeyFn = toy_seed_key,
               publish: Callable[[Finding], None] = lambda _f: None) -> bool:
    """Establish an extended session + security access. Returns True on success."""
    for finding in (start_session(transport, SESSION_EXTENDED),
                    security_access(transport, seed_key_fn)):
        publish(finding)
        if finding.is_failure:
            return False
    return True


def extract_binary(transport: Transport, start: int, size: int,
                   publish: Callable[[Finding], None] = lambda _f: None,
                   progress: Callable[[int, int], None] | None = None) -> bytes | None:
    """Read the ECU image (0x35/0x36) → returns bytes (the caller saves original_ecu_dump.bin)."""
    try:
        data = request_upload(transport, start, size, progress=progress)
    except UdsError as exc:
        publish(Finding(Source.HARDWARE, Severity.FAIL, "Extract failed", str(exc)))
        return None
    publish(Finding(Source.HARDWARE, Severity.OK, "ECU binary extracted",
                    f"{len(data):,} bytes read via 0x35/0x36."))
    return data


def write_binary(transport: Transport, start: int, data: bytes,
                 seed_key_fn: SeedKeyFn = toy_seed_key,
                 publish: Callable[[Finding], None] = lambda _f: None,
                 progress: Callable[[int, int], None] | None = None) -> bool:
    """Full write-back: programming session → security → download → ECU reset."""
    steps = [start_session(transport, SESSION_PROGRAMMING),
             security_access(transport, seed_key_fn)]
    for finding in steps:
        publish(finding)
        if finding.is_failure:
            return False
    download = request_download(transport, start, data, progress=progress)
    publish(download)
    if download.is_failure:
        return False
    reset = ecu_reset(transport)
    publish(reset)
    return not reset.is_failure
