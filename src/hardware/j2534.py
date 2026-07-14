"""Real-cable transport: Tactrix OpenPort 2.0 / J2534 PassThru via ctypes.

⚠ NOT VERIFIED IN THIS BUILD. There is no cable and no vehicle here, so this path cannot be
exercised — it is the honest seam the client validates on the vehicle (CLAUDE.md Section 3). It
implements the same :class:`~src.hardware.uds.Transport` interface as ``MockEcu``, so the UDS
logic that is proven against the mock drives the real ECU unchanged once the client plugs in.

It wraps the standard J2534 v04.04 API (``PassThruOpen``/``Connect``/``StartMsgFilter``/
``WriteMsgs``/``ReadMsgs``): opens an ISO15765 (CAN, 500 kbps) channel, installs the flow-control
filter for the request/response CAN IDs, and maps ``send``/``recv`` onto whole UDS messages. The
device performs the ISO-TP (ISO 15765-2) fragmentation/reassembly, so a multi-KB TransferData
block goes out as many CAN frames and comes back reassembled — ``uds.py`` keeps each block within
the 4095-byte ISO-TP limit. The driver DLL is discovered from the Windows registry
(``PassThruSupport.04.04``) or an explicit path; loading is deferred to :meth:`open` so importing
this module never requires the driver.

⚠ **32-bit runtime note.** OEM/Tactrix J2534 DLLs are almost always **32-bit**. Loading a 32-bit
DLL from a 64-bit Python raises ``OSError`` **WinError 193** ("%1 is not a valid Win32
application"), so the real-cable path typically needs the tool run under a **32-bit Python**.
:meth:`open` detects WinError 193 and says exactly that. (The mock backend has no such
constraint — it needs no driver at all.)
"""

from __future__ import annotations

import ctypes
import sys

# ── J2534 constants (v04.04) ──────────────────────────────────────────────────
STATUS_NOERROR = 0
PROTOCOL_ISO15765 = 6
BAUD_500K = 500_000
CONNECT_FLAGS_11BIT = 0x00000000
TXFLAG_ISO15765_FRAME_PAD = 0x00000040
RXSTATUS_TX_MSG_TYPE = 0x00000001  # a loopback echo of our own transmit — skip it
FILTER_FLOW_CONTROL = 3

# Default 11-bit UDS CAN identifiers (functional/physical vary by ECU — client confirms these).
DEFAULT_TX_ID = 0x7E0
DEFAULT_RX_ID = 0x7E8

# Tactrix OpenPort 2.0 default library name (see the client spec's driver path).
DEFAULT_DLL = "op20pt32.dll"


class J2534Error(Exception):
    """A J2534/driver-level failure (including 'driver or cable not present here')."""


class _PassThruMsg(ctypes.Structure):
    _fields_ = [
        ("ProtocolID", ctypes.c_ulong),
        ("RxStatus", ctypes.c_ulong),
        ("TxFlags", ctypes.c_ulong),
        ("Timestamp", ctypes.c_ulong),
        ("DataSize", ctypes.c_ulong),
        ("ExtraDataIndex", ctypes.c_ulong),
        ("Data", ctypes.c_ubyte * 4128),
    ]


def list_j2534_devices() -> list[tuple[str, str]]:
    """Enumerate installed J2534 devices from the registry: ``[(name, dll_path), …]``.

    Reads ``HKLM\\SOFTWARE\\[WOW6432Node\\]PassThruSupport.04.04``. Returns an empty list when
    no driver is installed (as in this build environment). The UI can offer these by name.
    """
    if sys.platform != "win32":
        return []
    try:
        import winreg
    except ImportError:
        return []

    devices: list[tuple[str, str]] = []
    for base_path in (r"SOFTWARE\WOW6432Node\PassThruSupport.04.04",
                      r"SOFTWARE\PassThruSupport.04.04"):
        try:
            base = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base_path)
        except OSError:
            continue
        with base:
            index = 0
            while True:
                try:
                    subkey_name = winreg.EnumKey(base, index)
                except OSError:
                    break
                index += 1
                try:
                    with winreg.OpenKey(base, subkey_name) as device:
                        dll = winreg.QueryValueEx(device, "FunctionLibrary")[0]
                        try:
                            name = winreg.QueryValueEx(device, "Name")[0]
                        except OSError:
                            name = subkey_name
                        devices.append((str(name), str(dll)))
                except OSError:
                    continue
        if devices:
            break
    return devices


def discover_driver_dll() -> str | None:
    """Return the first installed J2534 device's DLL path, or None."""
    devices = list_j2534_devices()
    return devices[0][1] if devices else None


class J2534Transport:
    """Tactrix J2534 implementation of the UDS ``Transport`` seam (validate on vehicle)."""

    def __init__(self, dll_path: str | None = None, tx_id: int = DEFAULT_TX_ID,
                 rx_id: int = DEFAULT_RX_ID) -> None:
        self._dll_path = dll_path
        self._tx_id = tx_id
        self._rx_id = rx_id
        self._dll: ctypes.CDLL | None = None
        self._device_id = ctypes.c_ulong(0)
        self._channel_id = ctypes.c_ulong(0)

    # ── lifecycle ─────────────────────────────────────────────────────────────
    def open(self) -> None:
        """Load the driver, open the device, connect the ISO15765 channel, install the filter.

        Raises :class:`J2534Error` with a clear message when the driver/cable is absent — which
        is always the case in this build environment.
        """
        if sys.platform != "win32":
            raise J2534Error("J2534 is Windows-only; run on the technician's Windows laptop.")
        dll_path = self._dll_path or discover_driver_dll() or DEFAULT_DLL
        try:
            self._dll = ctypes.WinDLL(dll_path)
        except OSError as exc:
            if getattr(exc, "winerror", None) == 193:
                raise J2534Error(
                    f"Cannot load the J2534 driver ({dll_path}): it is a 32-bit DLL but this is a "
                    f"64-bit Python (WinError 193). Run the tool under a 32-bit Python to use the "
                    f"real cable, then validate on the vehicle."
                ) from exc
            raise J2534Error(
                f"Tactrix J2534 driver not found ({dll_path}). Install the OpenPort 2.0 drivers "
                f"and connect the cable, then validate on the vehicle. [{exc}]"
            ) from exc

        self._check(self._dll.PassThruOpen(None, ctypes.byref(self._device_id)), "PassThruOpen")
        self._check(
            self._dll.PassThruConnect(self._device_id, PROTOCOL_ISO15765, CONNECT_FLAGS_11BIT,
                                      BAUD_500K, ctypes.byref(self._channel_id)),
            "PassThruConnect",
        )
        self._install_flow_control_filter()

    def close(self) -> None:
        if self._dll is None:
            return
        try:
            if self._channel_id.value:
                self._dll.PassThruDisconnect(self._channel_id)
            if self._device_id.value:
                self._dll.PassThruClose(self._device_id)
        finally:
            self._dll = None

    # ── Transport seam ────────────────────────────────────────────────────────
    def send(self, data: bytes) -> None:
        msg = self._build_msg(self._tx_id.to_bytes(4, "big") + data, TXFLAG_ISO15765_FRAME_PAD)
        count = ctypes.c_ulong(1)
        self._check(self._dll.PassThruWriteMsgs(self._channel_id, ctypes.byref(msg),
                                                ctypes.byref(count), 1000), "PassThruWriteMsgs")

    def recv(self, timeout: float = 2.0) -> bytes:
        deadline_ms = int(timeout * 1000)
        while deadline_ms > 0:
            msg = _PassThruMsg()
            count = ctypes.c_ulong(1)
            status = self._dll.PassThruReadMsgs(self._channel_id, ctypes.byref(msg),
                                                ctypes.byref(count), 200)
            deadline_ms -= 200
            if status != STATUS_NOERROR or count.value == 0:
                continue
            if msg.RxStatus & RXSTATUS_TX_MSG_TYPE:
                continue  # our own transmit echoed back
            if msg.DataSize > 4:  # strip the 4-byte CAN id, return the UDS payload
                return bytes(msg.Data[4:msg.DataSize])
        return b""

    # ── internals ─────────────────────────────────────────────────────────────
    def _build_msg(self, data: bytes, tx_flags: int) -> _PassThruMsg:
        msg = _PassThruMsg()
        msg.ProtocolID = PROTOCOL_ISO15765
        msg.TxFlags = tx_flags
        msg.DataSize = len(data)
        for i, byte in enumerate(data):
            msg.Data[i] = byte
        return msg

    def _install_flow_control_filter(self) -> None:
        """ISO15765 needs a flow-control filter mapping the response/request CAN ids."""
        mask = self._build_msg(b"\xff\xff\xff\xff", 0)
        pattern = self._build_msg(self._rx_id.to_bytes(4, "big"), 0)
        flow = self._build_msg(self._tx_id.to_bytes(4, "big"), 0)
        filter_id = ctypes.c_ulong(0)
        self._check(
            self._dll.PassThruStartMsgFilter(self._channel_id, FILTER_FLOW_CONTROL,
                                             ctypes.byref(mask), ctypes.byref(pattern),
                                             ctypes.byref(flow), ctypes.byref(filter_id)),
            "PassThruStartMsgFilter",
        )

    def _check(self, status: int, call: str) -> None:
        if status != STATUS_NOERROR:
            raise J2534Error(f"{call} failed with J2534 status {status}.")
