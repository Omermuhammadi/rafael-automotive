# Multi-Vehicle Diagnostic & Calibration Tool (MVP)

A Python desktop tool for automotive diagnostics and calibration, built around **two pillars**:

- **Binary Studio (Tab 1)** — load a raw ECU binary; hex view with offsets; read CALID/CVN;
  apply byte patches at defined offsets; validate & repair checksums; and read from / write back
  to an ECU over a UDS/J2534 hardware bridge (software-validated here; on-vehicle validation is
  done on the client's side).
- **OCPP Sniffer (Tab 3)** — a local mock CSMS + a simulated charge point; a colour-coded live
  frame log; an 8-scenario fault library; a click-a-line JSON deep-dive inspector; and a remote
  station reset. Everything runs with **zero hardware**.

Both pillars are fully demoable with only the bundled sample data and the software mock backends.

---

## Quick start

```bash
pip install -r requirements.txt      # Python 3.11+
python -m samples.make_sample        # generate samples/synthetic_ecu.bin (the demo fixture)
python -m src.main                   # launch the app (two-tab dark window)
```

Then:

- **Tab 1:** `Load Bin File` → pick `samples/synthetic_ecu.bin` → the hex view and CALID/CVN
  populate. `Load Patches` → pick `samples/example_patches.json` → choose a patch → `Apply Fix`
  → `Validate` (goes **red: mismatch**) → `Patch Checksum` (goes **green: PASS**) →
  `Export .bin`.
- **Tab 3:** `Start Central System Proxy` → pick a `Scenario` → `Run Scenario`. Watch the
  colour-coded frames; click a **red** line to inspect its JSON; try `Force Remote Station Reset`.

Headless OCPP demo (no UI, no hardware):

```bash
python -m src.ocpp_triage.scenarios --list
python -m src.ocpp_triage.scenarios --run all
python -m src.ocpp_triage.scenarios --run invalid_auth
```

Run the tests:

```bash
pytest -q
```

---

## Pillar A — Binary Studio (Tab 1)

**File workflow**

1. `Load Bin File` — loads `.bin` (raw) or `.hex` / `.s19` (Intel-HEX / Motorola S-record via
   `bincopy`). The hex viewer is virtualised, so a 1 MiB image scrolls instantly. `Go to:`
   jumps to an offset (`0x1A2F0` or `32`).
2. **Calibration Identifiers** (right sidebar) are read at fixed offsets from the loaded layout
   (CALID / CVN).
3. **Patch → checksum:** `Load Patches` reads an external patch-definition file (see below),
   `Apply Fix` writes the bytes (verifying the original bytes first), `Validate` / `Patch
   Checksum` run the selected algorithm (`crc32` / `crc16` / `blocksum`), and `Export .bin`
   writes the result (default `patched_ecu_release.bin`).

**Loading a patch file.** Patch definitions live in the *user's own* runtime file — they are
never committed to this repo (it stays a neutral byte-patcher). The bundled
`samples/example_patches.json` has offsets that line up with `synthetic_ecu.bin` so the flow
demos out of the box. Format:

```json
{
  "target": "example_ecu_1mb",
  "patches": [
    { "id": "example_patch", "description": "free-text note",
      "offset": "0x01A2F0", "original": "AA BB CC", "bytes": "00 00 00" }
  ]
}
```

`original` is optional; when present it is verified before writing, so applying a patch to the
wrong file (or wrong offset) fails loudly instead of corrupting the image.

**ECU Interface (hardware bridge).** The `ECU Interface` row drives the read/write-back to an
ECU over UDS:

- `Mock ECU` (default) — a pure-software ECU. `Initialize Interface` runs the session +
  seed/key handshake; `Extract ECU Binary` reads the image (UDS `0x35`/`0x36`) to
  `original_ecu_dump.bin` and loads it into the view; `Write Binary to ECU` flashes the current
  buffer back (`0x34`/`0x36`/`0x11`). No hardware needed.
- `Tactrix J2534` — the real cable path (`hardware/j2534.py`), interchangeable with the mock via
  the same `Transport` seam. **This path is validated on the vehicle, not in this build.** See
  the note below.

### Switching to the real Tactrix / J2534 cable

The `Tactrix J2534` backend wraps the standard J2534 v04.04 API over ISO15765 (CAN, 500 kbps);
the pass-through device performs the ISO-TP framing. To use it on the vehicle:

1. Install the **Tactrix OpenPort 2.0** drivers (the J2534 `.dll`, typically under
   `C:\Program Files (x86)\OpenECU\OpenPort 2.0\drivers\`). Installed J2534 devices are read from
   the Windows registry and offered by name in the interface dropdown.
2. **Run the tool under a 32-bit Python.** OEM/Tactrix J2534 DLLs are almost always 32-bit;
   loading a 32-bit DLL from a 64-bit Python fails with `OSError` **WinError 193**. The app
   detects this and tells you. (The mock backend has no such constraint.)
3. Plug the cable into the OBD-II port, select the device, and run Initialize → Extract → (edit)
   → Write as above. The seed/key algorithm is pluggable per ECU; the toy algorithm is for the
   mock only.

> The real seed/key handshake only completes against a real ECU, and the driver only talks to
> the real USB cable — neither can be verified without the hardware, which is why on-vehicle
> validation happens on the client's side.

---

## Pillars C + D — OCPP Sniffer (Tab 3)

- `Start Central System Proxy` binds a local mock CSMS on `ws://localhost:9000`, speaking
  **OCPP 1.6J** (the subprotocol is negotiated and locked).
- `Scenario` + `Run Scenario` plays a simulated charge point through one of **8 scenarios**:
  happy path, invalid auth token, auth timeout, malformed payload, rejected boot, transaction
  invalid, subprotocol mismatch, and remote station reset.
- The **frame log** colour-codes every frame: green = accepted, orange = in flight, red =
  failed. Click a red line → the **Frame Inspector** shows the JSON with the offending field
  highlighted and a plain-language explanation.
- `Force Remote Station Reset` issues `Reset.req {"type":"Hard"}` to the connected charge point.

The same scenarios run headless via `python -m src.ocpp_triage.scenarios --run all`.

**Using it with a real charging station** (the expected next step): point the station's
Central-System / backend URL at `ws://<laptop-ip>:9000`. The sniffer, the colour-coded triage,
the JSON inspector, and remote reset work identically against real OCPP 1.6J traffic — the
simulated charge point above is only for demoing without a station.

---

## Project layout

```
src/
  main.py                     # entry point: python -m src.main
  core/         findings.py, logbus.py     # shared Finding model + thread-safe log bus
  app/          theme.py, shell.py         # dark two-tab window
  binary_studio/ binfile, hexview, identifiers, patches, checksum, panel   # Pillar A
  ocpp_triage/  triage, sniffer, csms, charge_point_sim, scenarios, inspector, panel   # C+D
  hardware/     uds.py, mock_ecu.py, j2534.py     # Pillar A hardware bridge
samples/        make_sample.py, example_patches.json     # generated test data
tests/          pytest suite (logic is headless-testable; GUI tests skip without a display)
docs/           SCOPE.md, BUILD_PLAN.md, DEMO_CHECKLIST.md, requirements_traceability.html
```

**Design notes.** UI and logic are separated — every logic module is headless-testable with no
UI imports; `panel.py` files only wire finished logic to widgets. Background work (the async
OCPP server/client, and blocking hardware I/O) runs off the UI thread and reports to the UI
through the thread-safe `LogBus`. The hardware bridge sits behind a swappable `Transport` seam
so the mock and the real cable are interchangeable.

## Running & packaging

- **From source:** `python -m src.main` (or double-click `run.bat` on Windows).
- **Standalone `.exe` (optional):** build with PyInstaller. CustomTkinter ships asset files that
  must be bundled explicitly:

  ```bash
  pip install pyinstaller
  python -c "import customtkinter, os; print(os.path.dirname(customtkinter.__file__))"
  pyinstaller --noconfirm --windowed --name MVDCT ^
      --add-data "<that customtkinter path>;customtkinter/" ^
      src/main.py
  ```

  > If you also want the real **Tactrix J2534** path in the packaged app, build with a **32-bit
  > Python** (the OEM J2534 DLLs are 32-bit — see the cable note above).

## Scope

This build delivers exactly the two pillars above. Out of scope (by agreement): live OBD-II
telematics, OCPP 2.0.1, automatic map discovery, a universal manufacturer-checksum engine, and
on-vehicle validation of the read/write path (done on the client's vehicles). See `docs/SCOPE.md`.
