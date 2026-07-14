"""Tests for the Pillar A hardware bridge: UDS sequence + mock ECU round-trip + J2534 seam."""

from __future__ import annotations

import pytest

from src.core.findings import Severity, Source
from src.hardware import uds
from src.hardware.j2534 import J2534Error, J2534Transport
from src.hardware.mock_ecu import MockEcu


def _image(n: int) -> bytes:
    return bytes((i * 3 + 5) & 0xFF for i in range(n))


def test_initialize_then_extract_roundtrips() -> None:
    image = _image(20_000)  # spans several transfer blocks
    ecu = MockEcu(image=image)
    findings: list = []
    assert uds.initialize(ecu, publish=findings.append) is True
    data = uds.extract_binary(ecu, 0, len(image), publish=findings.append)
    assert data == image
    assert all(f.source is Source.HARDWARE for f in findings)


def test_extract_edit_writeback_roundtrip() -> None:
    image = _image(20_000)
    ecu = MockEcu(image=image)
    assert uds.initialize(ecu)
    data = uds.extract_binary(ecu, 0, len(image))
    assert data is not None

    modified = bytearray(data)
    modified[0x1000:0x1008] = b"\xDE\xAD\xBE\xEF\x00\x11\x22\x33"
    assert uds.write_binary(ecu, 0, bytes(modified)) is True
    assert bytes(ecu.memory) == bytes(modified)  # the ECU now holds the patched image


def test_command_sequence_matches_spec() -> None:
    ecu = MockEcu(image=_image(9000))
    uds.initialize(ecu)
    uds.extract_binary(ecu, 0, 9000)
    uds.write_binary(ecu, 0, _image(9000))
    log = ecu.request_log

    assert log[0] == uds.SID_DIAGNOSTIC_SESSION_CONTROL  # 0x10 first
    for sid in (uds.SID_SECURITY_ACCESS, uds.SID_REQUEST_UPLOAD, uds.SID_TRANSFER_DATA,
                uds.SID_REQUEST_TRANSFER_EXIT, uds.SID_REQUEST_DOWNLOAD):
        assert sid in log
    assert log[-1] == uds.SID_ECU_RESET  # 0x11 last (write-back ends with a reset)


def test_extract_requires_security() -> None:
    ecu = MockEcu(image=_image(4096))  # never initialized → locked, default session
    with pytest.raises(uds.UdsError):
        uds.request_upload(ecu, 0, 4096)


def test_invalid_key_is_denied() -> None:
    ecu = MockEcu(image=_image(4096))
    uds.start_session(ecu)
    finding = uds.security_access(ecu, seed_key_fn=lambda seed: b"\x00\x00\x00\x00")
    assert finding.severity is Severity.FAIL


def test_start_session_finding_shape() -> None:
    finding = uds.start_session(MockEcu(image=_image(256)))
    assert finding.source is Source.HARDWARE and finding.severity is Severity.OK


def test_response_pending_0x78_is_handled() -> None:
    # ECU replies 0x78 (busy) three times before the real seed → client must keep waiting.
    ecu = MockEcu(image=_image(256), pending_service=uds.SID_SECURITY_ACCESS, pending_count=3)
    uds.start_session(ecu)
    assert uds.security_access(ecu).severity is Severity.OK


def test_excessive_response_pending_gives_up_without_hanging() -> None:
    ecu = MockEcu(image=_image(256), pending_service=uds.SID_SECURITY_ACCESS, pending_count=40)
    uds.start_session(ecu)
    finding = uds.security_access(ecu)  # bounded by _MAX_PENDING → returns, never hangs
    assert finding.severity is Severity.FAIL


def test_transfer_block_capped_within_isotp_limit() -> None:
    advertises_4gb_block = bytes([0x75, 0x40, 0xFF, 0xFF, 0xFF, 0xFF])
    assert uds._data_per_block(advertises_4gb_block) == uds.ISOTP_MAX_MESSAGE - 2


def test_large_transfer_reassembles_across_seq_wrap() -> None:
    # 8 data bytes/block over 3000 bytes = 375 blocks → crosses the 0xFF→0x00 block-seq wrap.
    image = _image(3000)
    ecu = MockEcu(image=image, max_block=10)
    assert uds.initialize(ecu)
    assert uds.extract_binary(ecu, 0, len(image)) == image

    modified = bytearray(image)
    modified[100:108] = b"\x01\x02\x03\x04\x05\x06\x07\x08"
    assert uds.write_binary(ecu, 0, bytes(modified)) is True
    assert bytes(ecu.memory) == bytes(modified)


def test_list_j2534_devices_returns_list() -> None:
    from src.hardware.j2534 import list_j2534_devices
    assert isinstance(list_j2534_devices(), list)  # empty here; never crashes


def test_j2534_open_without_driver_raises_clearly() -> None:
    with pytest.raises(J2534Error):
        J2534Transport(dll_path="definitely_not_a_real_driver_xyz.dll").open()
