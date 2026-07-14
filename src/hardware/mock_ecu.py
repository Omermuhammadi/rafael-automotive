"""A pure-software ECU that answers the UDS sequence — no cable, no car.

``MockEcu`` implements the :class:`~src.hardware.uds.Transport` seam (``send``/``recv``) as a
request/response state machine over an in-memory flash image. It enforces the real gating —
you must be in a non-default session and have completed the seed/key handshake before it will
upload or download — so the tests exercise the same order of operations the vehicle requires.

It records every service id it receives in :attr:`request_log`, which the tests assert against
to prove the extract/write-back command sequence is correct.
"""

from __future__ import annotations

from src.hardware.uds import (
    ADDR_LEN_FORMAT_4_4,  # noqa: F401  (documented request shape)
    POSITIVE_RESPONSE_OFFSET,
    RESET_HARD,
    SECURITY_REQUEST_SEED,
    SECURITY_SEND_KEY,
    SESSION_DEFAULT,
    SID_DIAGNOSTIC_SESSION_CONTROL,
    SID_ECU_RESET,
    SID_REQUEST_DOWNLOAD,
    SID_REQUEST_TRANSFER_EXIT,
    SID_REQUEST_UPLOAD,
    SID_SECURITY_ACCESS,
    SID_TRANSFER_DATA,
    SeedKeyFn,
    toy_seed_key,
)

_NEGATIVE_RESPONSE = 0x7F
DEFAULT_SIZE = 0x100000  # 1 MiB flash
DEFAULT_SEED = b"\x11\x22\x33\x44"
# advertised maxNumberOfBlockLength: 2048 data bytes + 2 overhead — comfortably within one
# ISO-TP message on the real cable (uds.py caps it defensively regardless).
_MAX_BLOCK = 0x0802

# Negative response codes
_NRC_SERVICE_NOT_SUPPORTED = 0x11
_NRC_SUBFUNCTION_NOT_SUPPORTED = 0x12
_NRC_CONDITIONS_NOT_CORRECT = 0x22
_NRC_REQUEST_OUT_OF_RANGE = 0x31
_NRC_SECURITY_ACCESS_DENIED = 0x33
_NRC_INVALID_KEY = 0x35
_NRC_WRONG_BLOCK_SEQUENCE = 0x73
_NRC_RESPONSE_PENDING = 0x78


def default_image(size: int = DEFAULT_SIZE) -> bytes:
    """A deterministic, recognisable flash image for when no image is supplied."""
    buf = bytearray((i * 7 + 0x11) & 0xFF for i in range(size))
    buf[0:16] = b"MOCK-ECU-FLASH\x00\x00"
    return bytes(buf)


class MockEcu:
    """Software ECU implementing the UDS ``Transport`` seam."""

    def __init__(self, image: bytes | None = None, size: int = DEFAULT_SIZE,
                 seed: bytes = DEFAULT_SEED, seed_key_fn: SeedKeyFn = toy_seed_key,
                 max_block: int = _MAX_BLOCK, pending_service: int | None = None,
                 pending_count: int = 0) -> None:
        self.memory = bytearray(image if image is not None else default_image(size))
        self._seed = seed
        self._seed_key_fn = seed_key_fn
        self._max_block = max_block
        # Optionally emit `pending_count` responsePending (0x78) replies before the real
        # response to a given service, so the client's 0x78 handling can be tested.
        self._pending_service = pending_service
        self._pending_remaining = pending_count
        self.request_log: list[int] = []
        self._session = SESSION_DEFAULT
        self._unlocked = False
        self._seed_issued = False
        self._transfer: dict | None = None
        self._responses: list[bytes] = []

    # ── Transport seam ────────────────────────────────────────────────────────
    def open(self) -> None:
        """No-op: the mock needs no driver or hardware."""

    def close(self) -> None:
        """No-op."""

    def send(self, data: bytes) -> None:
        req = bytes(data)
        real = self._handle(req)
        sid = req[0] if req else 0
        queue: list[bytes] = []
        if sid == self._pending_service and self._pending_remaining > 0:
            queue = [self._neg(sid, _NRC_RESPONSE_PENDING)] * self._pending_remaining
            self._pending_remaining = 0
        queue.append(real)
        self._responses = queue

    def recv(self, timeout: float = 2.0) -> bytes:
        return self._responses.pop(0) if self._responses else b""

    # ── helpers ───────────────────────────────────────────────────────────────
    @staticmethod
    def _pos(sid: int, *extra: int) -> bytes:
        return bytes([sid + POSITIVE_RESPONSE_OFFSET, *extra])

    @staticmethod
    def _neg(sid: int, nrc: int) -> bytes:
        return bytes([_NEGATIVE_RESPONSE, sid, nrc])

    def _gate(self, sid: int) -> bytes | None:
        """Uploads/downloads require a non-default session and a completed seed/key handshake."""
        if self._session == SESSION_DEFAULT:
            return self._neg(sid, _NRC_CONDITIONS_NOT_CORRECT)
        if not self._unlocked:
            return self._neg(sid, _NRC_SECURITY_ACCESS_DENIED)
        return None

    # ── request routing ───────────────────────────────────────────────────────
    def _handle(self, req: bytes) -> bytes:
        if not req:
            return self._neg(0x00, _NRC_SERVICE_NOT_SUPPORTED)
        sid = req[0]
        self.request_log.append(sid)
        handler = {
            SID_DIAGNOSTIC_SESSION_CONTROL: self._session_control,
            SID_ECU_RESET: self._ecu_reset,
            SID_SECURITY_ACCESS: self._security_access,
            SID_REQUEST_UPLOAD: self._request_upload,
            SID_REQUEST_DOWNLOAD: self._request_download,
            SID_TRANSFER_DATA: self._transfer_data,
            SID_REQUEST_TRANSFER_EXIT: self._transfer_exit,
        }.get(sid)
        if handler is None:
            return self._neg(sid, _NRC_SERVICE_NOT_SUPPORTED)
        return handler(req)

    def _session_control(self, req: bytes) -> bytes:
        session = req[1] if len(req) > 1 else 0
        if session != self._session:  # switching session drops security
            self._unlocked = False
            self._seed_issued = False
        self._session = session
        return self._pos(SID_DIAGNOSTIC_SESSION_CONTROL, session)

    def _security_access(self, req: bytes) -> bytes:
        sub = req[1] if len(req) > 1 else 0
        if sub == SECURITY_REQUEST_SEED:
            self._seed_issued = True
            if self._unlocked:
                return self._pos(SID_SECURITY_ACCESS, sub, 0, 0, 0, 0)  # zero seed = unlocked
            return self._pos(SID_SECURITY_ACCESS, sub, *self._seed)
        if sub == SECURITY_SEND_KEY:
            if not self._seed_issued:
                return self._neg(SID_SECURITY_ACCESS, _NRC_CONDITIONS_NOT_CORRECT)
            if bytes(req[2:]) == self._seed_key_fn(self._seed):
                self._unlocked = True
                return self._pos(SID_SECURITY_ACCESS, sub)
            return self._neg(SID_SECURITY_ACCESS, _NRC_INVALID_KEY)
        return self._neg(SID_SECURITY_ACCESS, _NRC_SUBFUNCTION_NOT_SUPPORTED)

    def _request_upload(self, req: bytes) -> bytes:
        gate = self._gate(SID_REQUEST_UPLOAD)
        if gate is not None:
            return gate
        addr = int.from_bytes(req[3:7], "big")
        size = int.from_bytes(req[7:11], "big")
        if addr + size > len(self.memory):
            return self._neg(SID_REQUEST_UPLOAD, _NRC_REQUEST_OUT_OF_RANGE)
        self._transfer = {"mode": "upload", "addr": addr, "size": size, "offset": 0}
        return self._pos(SID_REQUEST_UPLOAD, 0x20, *self._max_block.to_bytes(2, "big"))

    def _request_download(self, req: bytes) -> bytes:
        gate = self._gate(SID_REQUEST_DOWNLOAD)
        if gate is not None:
            return gate
        addr = int.from_bytes(req[3:7], "big")
        size = int.from_bytes(req[7:11], "big")
        if addr + size > len(self.memory):
            return self._neg(SID_REQUEST_DOWNLOAD, _NRC_REQUEST_OUT_OF_RANGE)
        self._transfer = {"mode": "download", "addr": addr, "size": size, "offset": 0, "seq": 1}
        return self._pos(SID_REQUEST_DOWNLOAD, 0x20, *self._max_block.to_bytes(2, "big"))

    def _transfer_data(self, req: bytes) -> bytes:
        transfer = self._transfer
        if transfer is None:
            return self._neg(SID_TRANSFER_DATA, _NRC_CONDITIONS_NOT_CORRECT)
        seq = req[1] if len(req) > 1 else 0
        if transfer["mode"] == "upload":
            data_per_block = self._max_block - 2
            take = min(data_per_block, transfer["size"] - transfer["offset"])
            start = transfer["addr"] + transfer["offset"]
            chunk = bytes(self.memory[start:start + take])
            transfer["offset"] += take
            return self._pos(SID_TRANSFER_DATA, seq, *chunk)  # echo the tester's seq
        # download
        if seq != (transfer["seq"] & 0xFF):
            return self._neg(SID_TRANSFER_DATA, _NRC_WRONG_BLOCK_SEQUENCE)
        chunk = bytes(req[2:])
        start = transfer["addr"] + transfer["offset"]
        self.memory[start:start + len(chunk)] = chunk
        transfer["offset"] += len(chunk)
        transfer["seq"] = (transfer["seq"] + 1) & 0xFF
        return self._pos(SID_TRANSFER_DATA, seq)

    def _transfer_exit(self, req: bytes) -> bytes:
        self._transfer = None
        return self._pos(SID_REQUEST_TRANSFER_EXIT)

    def _ecu_reset(self, req: bytes) -> bytes:
        reset_type = req[1] if len(req) > 1 else RESET_HARD
        self._session = SESSION_DEFAULT
        self._unlocked = False
        self._seed_issued = False
        return self._pos(SID_ECU_RESET, reset_type)
