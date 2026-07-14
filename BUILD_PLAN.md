# BUILD_PLAN.md — 7-Day Phased Build

Two pillars, seven days, built with Claude Code. **Build in vertical slices, one phase at a
time.** Each phase ends with: tests green + the slice demoable. Do not jump ahead.

The ordering is deliberately **risk-first**: the certain, file-based software ships early; the
uncertain, client-validated hardware bridge is last so a time crunch never threatens the core
deliverable.

> **Tools, test data, and reuse.** Libraries named below are recommendations — swap for anything
> better-maintained or faster (see `CLAUDE.md §4`). **You generate all test data yourself**
> (`CLAUDE.md §5`); nothing is provided and nothing is needed. Reuse proven open-source rather
> than hand-rolling (OCPP simulators, a UDS library, J2534 wrappers, CRC routines). The function
> signatures in this plan are contracts to fill, not prescribed implementations.

| Day | Phase | Outcome |
|-----|-------|---------|
| 1   | 0 + start 1 | App shell opens with 2 tabs; binary loads and renders as hex |
| 2   | 1 + 2 | **Pillar A software complete** (identify → patch → checksum → output) |
| 3   | 2 wrap + 3 | OCPP core running: mock CSMS + simulated CP + basic triage |
| 4   | 3 wrap + 4 | **EV pillar complete** (scenarios, JSON inspector, remote reset) |
| 5   | 5 | Hardware bridge: UDS sequence + J2534 seam, validated vs mock ECU |
| 6   | 5 wrap + 6 | Integration, error handling, packaging |
| 7   | 6 | Buffer, final test pass, README, demo checklist, handoff |

Days are targets. The hard rule is the *sequence* and the *definition of done* per phase.

---

## Shared foundation (build first, in Phase 0)

### `core/findings.py`
The single reporting model every pillar emits. The UI colours rows by `Severity`.

```python
from dataclasses import dataclass, field
from enum import Enum
import time

class Severity(Enum):
    OK   = "ok"    # green  — accepted / passed
    INFO = "info"  # orange — auth / transaction in flight
    WARN = "warn"  # amber  — non-fatal
    FAIL = "fail"  # red    — rejected / timeout / malformed / mismatch

class Source(Enum):
    BINARY   = "binary_studio"
    OCPP     = "ocpp"
    HARDWARE = "hardware"

@dataclass
class Finding:
    source: Source
    severity: Severity
    title: str                 # short label, e.g. "Checksum mismatch"
    detail: str                # plain-language what + why
    raw: str | None = None     # payload/bytes/frame for the deep-dive panel
    ts: float = field(default_factory=time.time)
```

### `core/logbus.py`
A tiny thread-safe pub/sub so background loops (OCPP server, UDS transfer) can push `Finding`s
to the UI without touching Tk from another thread. `subscribe(callback)` / `publish(finding)`;
the UI drains it on a Tk `.after()` timer.

---

## Phase 0 — Scaffolding  *(Day 1, first half)*

- Repo tree exactly as in `CLAUDE.md §3`. `requirements.txt` installed.
- `app/shell.py`: CustomTkinter window, dark theme (`app/theme.py`), a `CTkTabview` with
  two tabs — **"Binary Studio"** and **"OCPP Sniffer"** — each an empty panel for now.
- `core/findings.py` + `core/logbus.py` in place.
- **Test data generator:** `samples/make_sample.py` → `samples/synthetic_ecu.bin` with known
  CALID/CVN at known offsets, a couple of known map regions, and a valid stored checksum. This
  is the ground-truth fixture for every later phase. (Optionally mirror a real ECU layout you
  research, for demo realism.)
- `main.py` launches the shell. `pytest` configured; one trivial passing test.

**Done when:** `python -m src.main` opens a two-tab dark window; `samples/synthetic_ecu.bin`
generates reproducibly; `pytest -q` is green.

---

## Phase 1 — Binary Studio: read + hex view + identifiers  *(Day 1 second half → Day 2)*

### `binary_studio/binfile.py`
```python
def load(path: str) -> bytearray      # .bin raw; .hex/.s19 via bincopy -> bytes
def save(path: str, buf: bytes) -> None
def size(buf: bytes) -> int
```

### `binary_studio/hexview.py`  (logic only — no UI)
Produce rows of `(offset, hex_bytes, ascii)` for a viewport, e.g.
`rows(buf, start, count, width=16) -> list[HexRow]`. The panel renders these into a scrollable
monospace view with an offset gutter.

### `binary_studio/identifiers.py`
CALID / CVN live at known offsets for a given ECU (and there's a standard OBD service for them
on the wire — but here we read them from the dump). Config-driven:
```python
def read_identifiers(buf: bytes, layout: dict) -> list[Finding]
# layout = {"calid": {"offset": "0x...", "len": N, "kind": "ascii"},
#           "cvn":   {"offset": "0x...", "len": N, "kind": "hex"}}
```
Emit `Finding`s (INFO) so they show in the metadata block. Use PDF's sample placeholders as
defaults for the demo (`CALID: 1267394012`, `CVN: 4A8B2C1E`).

### `binary_studio/panel.py` (Tab 1, part 1)
`[ Load Bin File ]` → hex view populates + offsets + identifier metadata block.

**Done when:** loading `samples/synthetic_ecu.bin` shows correct hex + offsets and reads the
identifiers; `test_binfile.py`, `test_identifiers.py` green.

---

## Phase 2 — Binary Studio: patch + checksum  *(Day 2 → Day 3 start)*

### Patch-definition format (external file, loaded at runtime — not committed)

**You create** `samples/example_patches.json` for the demo, with offsets/bytes matching the
synthetic binary, so the full apply→checksum flow works out of the box. The *format* is what the
end user supplies at runtime; real vehicle patches are theirs and are never hardcoded here.
```json
{
  "target": "example_ecu_1mb",
  "patches": [
    {
      "id": "example_patch",
      "description": "free-text note — user's domain",
      "offset": "0x01A2F0",
      "original": "AA BB CC",     // optional safety check before writing
      "bytes":    "00 00 00"
    }
  ]
}
```

### `binary_studio/patches.py`
```python
def load_patch_defs(path: str) -> list[Patch]
def apply_patch(buf: bytearray, patch: Patch, verify_original: bool = True) -> Finding
#   writes patch.bytes at patch.offset; if verify_original and patch.original is set,
#   confirm current bytes match first, else return a FAIL Finding (wrong file / wrong offset)
```
The dropdown in the UI is populated **from the loaded patch file**, not from hardcoded logic.

### `binary_studio/checksum.py`
```python
def crc16(data: bytes) -> int
def crc32(data: bytes) -> int
def block_sum(data: bytes, word: int = 1) -> int

def validate(buf: bytes, region: slice, stored: slice, algo: str) -> Finding   # OK/FAIL
def repair(buf: bytearray, region: slice, stored: slice, algo: str) -> Finding # writes new csum
```
`algo` ∈ {`crc16`,`crc32`,`blocksum`, +at most one ECU-specific scheme if the target needs it}.

### `binary_studio/panel.py` (Tab 1, part 2) — wire the full PDF workflow
`[ Load Patch File ]` → `[ Apply Fix ]` → `[ Validate Checksum ]` (goes **red: FAIL — mismatch**)
→ `[ Patch Checksum ]` (goes **green: PASS**, shows new CRC) → outputs `patched_ecu_release.bin`.

**Done when:** the load→patch→FAIL→repair→PASS→export flow works end to end on the sample;
`test_patches.py`, `test_checksum.py` green with known-good / known-bad golden files.
**➜ This is the software half of Pillar A — the part we guarantee.**

---

## Phase 3 — OCPP pillar: mock CSMS + simulated CP + triage  *(Day 3)*

Use the `ocpp` library's **v16** API and `websockets`. Follow the installed library's current
class names (payload class names have shifted across versions — match what's installed, don't
guess).

### `ocpp_triage/csms.py`  — the mock Central System (server)
`websockets.serve` on `ws://localhost:9000`. For each connection, an ocpp v16 `ChargePoint`
handler with `@on(...)` handlers for **BootNotification, Authorize, StartTransaction,
StopTransaction, Heartbeat, StatusNotification**. Each handler returns the appropriate
`call_result` and publishes a `Finding` to the logbus. Behaviour (accept / reject / delay) is
driven by the active scenario so we can force failures.

### `ocpp_triage/charge_point_sim.py`  — simulated charge point (client)
`websockets.connect` to the CSMS; an ocpp v16 `ChargePoint` that runs a session:
Boot → Authorize → StartTransaction → Heartbeat×N → StopTransaction. **This is what lets us
test with zero hardware.**

### `ocpp_triage/triage.py`
```python
def classify(direction: str, action: str, payload: dict,
             response: dict | None, elapsed: float | None) -> Finding
#   accepted            -> OK   (green)
#   auth/txn in flight  -> INFO (orange)
#   rejected/invalid/timeout/malformed -> FAIL (red) + plain-language reason
```

### `ocpp_triage/panel.py` (Tab 3, part 1)
`[ Start Local Central System Proxy ]` → live colour-coded frame log (green/orange/red) fed
from the logbus.

**Done when:** running sim-CP against the CSMS streams a full green session in the UI, and an
injected fault turns the right line red; `test_ocpp_triage.py` (async) green.

---

## Phase 4 — OCPP pillar: scenarios + inspector + remote reset  *(Day 4)*

### `ocpp_triage/scenarios.py` — the fault library (fixtures **and** live demo)
1. **Happy path** — Boot→Auth(valid)→Start→Heartbeat→Stop, all green.
2. **Invalid auth token** — Authorize → Blocked/Invalid → red.
3. **Auth timeout** — no response within N s → red timeout.
4. **Malformed payload** — StartTransaction missing/!schema field → red; inspector shows it.
5. **Rejected boot** — BootNotification → Rejected → red.
6. **Transaction invalid** — StartTransaction.conf `Status: Invalid` → red (matches PDF).
7. **Protocol/subprotocol mismatch** — unsupported subprotocol → surfaced connection error.
8. **Remote reset** — operator triggers `Reset.req {"type":"Hard"}` → CP acks/reboots.

Runnable headless: `python -m src.ocpp_triage.scenarios --run all`.

### `ocpp_triage/inspector.py` + panel
Click a red line → JSON sidebar showing the payload with the offending field/format flagged
(PDF's "deep-dive audit": bad token format / missing certification tag).

### Remote reset
`[ Force Remote Station Reset ]` → sends `Reset.req` type `Hard` to the sim CP (PDF Step 6).

**Done when:** all 8 scenarios run headless and in-UI with correct colours; inspector shows the
broken field; reset button works against the sim.
**➜ EV pillar complete and fully validated remotely.**

---

## Phase 5 — Pillar A hardware bridge (written, mock-validated)  *(Day 5 → Day 6)*

This is the "extract off / flash to the real vehicle" code. We write it and prove the **logic**
against a software ECU; the real cable is validated on the client's side (no cable/car here —
`CLAUDE.md §3`). **Recommended:** build this on a maintained UDS library (e.g. `udsoncan`) over
the transport seam instead of hand-writing the service bytes, and reuse a solid J2534 `ctypes`
wrapper if you find one. The signatures below are the contract; the implementation is yours.

### `hardware/uds.py` — UDS sequence behind a `Transport` seam
```python
class Transport(Protocol):
    def send(self, data: bytes) -> None: ...
    def recv(self, timeout: float) -> bytes: ...

def start_session(t, session=0x03)      -> Finding   # 0x10
def security_access(t, seed_key_fn)     -> Finding   # 0x27 seed → key
def request_upload(t, start, size)      -> bytes     # 0x35 then 0x36 loop → original_ecu_dump.bin
def request_download(t, start, data)    -> Finding   # 0x34 then 0x36 loop
def ecu_reset(t)                        -> Finding   # 0x11
```
`seed_key_fn` is **pluggable**. The mock ECU uses a known toy algorithm. The *real* per-ECU
seed/key is the client's to supply/verify — this is the honest seam for the locked-ECU reality.

### `hardware/mock_ecu.py` — software ECU
A `Transport` that answers the full UDS sequence (session, seed/key, upload/download, reset) so
`request_upload`/`request_download` round-trip a binary in software. Lets Tab 1's
`[ Initialize Interface ] / [ Extract ECU Binary ] / [ Write Binary to ECU ]` buttons run and
demo with **no car**.

### `hardware/j2534.py` — real-cable seam (ctypes)
A `Transport` implementation that loads the Tactrix J2534 `.dll` via `ctypes`, opens a CAN
channel (ISO15765, 500 kbps), and maps `send`/`recv` to PassThru calls. **Interchangeable**
with `mock_ecu` via the `Transport` protocol. Default the app to the mock backend; expose a
connection dropdown (`Mock ECU` / `Tactrix J2534`) so the client selects real hardware on their
vehicle. Do **not** claim this path is verified.

**Done when:** the full extract→(edit in Studio)→write-back flow round-trips against
`mock_ecu`; `test_uds.py` asserts the correct command sequence and reassembled binary; the
J2534 backend is present with a clear "validate on vehicle" marker.

---

## Phase 6 — Integration, polish, packaging  *(Day 6 → Day 7)*

- End-to-end walkthrough of both tabs. Empty states, error handling, no crashes on bad input.
- `README.md`: setup, run, how to load a patch file, how to switch to the Tactrix backend for
  on-vehicle validation, how to run the OCPP demo.
- A one-page **demo checklist** for showing the client (both pillars, no hardware needed).
- Final `pytest` pass. Keep Day 7 as buffer for slippage.

**Done when:** clean install → `python -m src.main` → both pillars demoable start to finish
with only sample files and the mock backend.

---

## If the week gets tight — cut in this order (last cut first)
1. Extra OCPP scenarios beyond the core 6 (keep happy-path + invalid-auth + timeout + malformed).
2. J2534 real-cable seam polish (keep the mock-validated UDS logic + the seam; the client's
   real validation is a separate step anyway).
3. Cosmetic UI polish.
**Never cut:** Pillar A file pipeline (Phases 1–2) or the EV pillar core (Phases 3–4). Those
are the guaranteed deliverable.
