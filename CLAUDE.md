# CLAUDE.md — Multi-Vehicle Diagnostic & Calibration Tool (MVP)

Master context for Claude Code. Read this fully before writing code.

Client spec: `docs/client_spec.pdf and client_spec v2 updated`. Scope + reasoning: `docs/SCOPE.md`.
Phased plan: `docs/BUILD_PLAN.md`.

**How to read this file.** Only two things are *fixed*: the **deliverable** (what the client
paid for) and the **environment** (no vehicle hardware in this build). Everything else — tools,
libraries, architecture, approach, optimisations — is **your call as the engineer.** Use your
full judgment. Research freely, reuse proven open-source work, pick the best tools, and make it
excellent. The fixed parts below are the contract and the physics, not a leash.

---

## 1. What we're building

A Python desktop tool (clean ui, tabbed) for automotive diagnostics and calibration. **Two pillars
in scope:**

- **Pillar A — Binary Studio (Tab 1):** load a raw ECU binary; hex view with offsets; read
  CALID/CVN; apply byte patches at defined offsets; validate + repair checksum; and via a
  hardware bridge, read from / write back to a real ECU over a Tactrix J2534 cable.
- **Pillars C + D — OCPP Sniffer (Tab 3):** local mock CSMS; simulated charge point; colour-
  coded live frame log; failure triage (bad auth, timeout, malformed); JSON deep-dive; remote
  station reset.

---

## 2. FIXED — the deliverable (the contract)

Defines *what ships*. Fixed because the client paid for it and the clock is 7 days — not
because your approach is constrained.

- **Two pillars only.** Pillar A + Pillars C/D. **Pillar B (live OBD-II) is not in this offer —
  don't build it.**
- **Patch-at-known-offset is the committed feature.** The tool writes bytes at addresses from an
  external patch-definition file. Using existing ECU definitions / known offset tables to locate
  things is **fine and encouraged.** What's **not** in scope: building a general from-scratch
  *auto-discovery* engine that reverse-engineers maps out of arbitrary unknown binaries. That's
  an open-ended research problem (what WinOLS-class tools charge thousands for) with no finish
  line, and it's the single most likely thing to miss the deadline. Scalpel, not surgeon.
- **Checksum ships CRC16 / CRC32 / block-sum** behind one interface. Add a specific ECU scheme
  only if it's cheap for the chosen target. No universal manufacturer-checksum engine.
- **The tool is a general-purpose byte patcher.** Specific patch definitions load from an
  external file at runtime — not hardcoded, not committed here. Keep the repo a neutral
  instrument.

## 3. FIXED — the environment (physics, not caution)

- **There is no Tactrix cable and no vehicle in this build environment.** So the J2534/UDS
  read-and-writeback code is proven against a **software mock ECU** that answers the UDS
  sequence, sitting behind a **swappable transport seam.** The *real* cable path implements the
  same seam and is validated by the client on their vehicle. Default to the mock everywhere. No
  cleverness verifies real-hardware I/O without the hardware.
- Client targets (2014 Charger 3.6, 2015 Silverado 5.3) are modern locked ECUs; their free
  seed/key is uncertain. That's exactly why the seam exists and why on-vehicle validation is the
  client's step, not ours.

---

## 4. YOURS — full engineering latitude

This is where you have the wheel. Optimise for a fast, clean build and a delighted client.

- **Tools & libraries are your choice.** The stack in §6 is a *starting point*, not a mandate.
  The PDF's picks (Cursor, CustomTkinter, specific libs) are **not** required — if a better-
  maintained, faster, or cleaner option exists, use it and leave a one-line note on why. Only
  constraint: the finished thing must run for the client (assume a Windows, technician-facing
  user).
- **Research and reuse — don't reinvent.** Pull proven open-source and adapt it: OCPP
  simulators, UDS libraries (e.g. `udsoncan`), J2534 `ctypes` wrappers, CRC routines, ECU
  dump/definition formats. Standing on existing work is the fast path.
- **Generate your own test data — never wait on anyone.** See §5. Only flag the user if
  something genuinely cannot proceed without the client's *real* hardware (for the software MVP,
  that won't happen).
- **Optimise and exceed the spec** wherever you can do so *without* risking the deadline or the
  two fixed sections above. Faster approaches, better UX, cleaner architecture — go for it.

---

## 5. Test data — you create it

No data is provided by the user or client, and none is needed.

- **Primary — synthesise a controlled sample binary** in Phase 0. Write a generator
  (`samples/make_sample.py`) producing `samples/synthetic_ecu.bin` with **known** values:
  CALID/CVN strings at known offsets, a couple of known 2D/3D map regions, and a valid stored
  checksum. Known ground truth = trustworthy tests for hex view, identifiers, patching, checksum.
- **Optional realism** — research a real ECU dump layout / open definition format (RomRaider XML,
  TunerPro XDF, public sample dumps) and mirror its structure so the demo feels real.
- **Example patch file** — create `samples/example_patches.json` whose offsets/bytes line up with
  the synthetic binary, so the full apply→checksum flow demos out of the box.
- The client's *real* dump only matters for *their* on-vehicle validation. Not a build dependency.

---

## 6. Recommended stack (starting point — swap freely, see §4)

- **Python 3.11+**
- **UI:** CustomTkinter (dark, tabbed) by default. If a live colour-coded log is cleaner in a
  framework you prefer (PySide/Qt, a Textual TUI, or a small local web UI), fine — keep it
  runnable by a Windows technician.
- **Binary:** `bincopy` (.hex/.s19/.bin), `crccheck` (CRC16/32), `numpy` (maps, light).
- **OCPP:** `ocpp` (v16 classes; 1.6J, not 2.0.1) + `websockets`. Adapt the library's own
  examples / a solid open-source OCPP simulator rather than starting cold.
- **UDS / hardware:** prefer a maintained UDS library (e.g. `udsoncan`) over hand-writing service
  bytes; wrap the Tactrix J2534 `.dll` via `ctypes` behind the transport seam; reuse a good
  J2534 python wrapper if you find one.
- **Tests:** `pytest` + `pytest-asyncio`.

> Simulation ≠ testing: `unittest.mock` is for unit tests. The *live* sims are real components
> (`mock_ecu`, `charge_point_sim`), not mocks. Don't confuse the two (the PDF does). And no IDE
> provides emulation — it's all in the code and runs in any terminal.

---

## 7. Conventions (quality bar — applies whatever tools you pick)

- **UI and logic separate.** Logic modules have zero UI imports and are fully headless-testable.
  `panel.py` files only wire finished logic to widgets.
- Type hints, dataclasses, small single-purpose modules. Tests alongside each logic module;
  golden fixtures for binary/checksum.
- Async OCPP off the UI thread; marshal events to the UI via a log bus.
- No secrets; no network beyond localhost websockets.

---

## 8. Running

```bash
pip install -r requirements.txt
python -m src.main                              # launch the app
pytest -q                                       # all tests
python -m src.ocpp_triage.scenarios --run all   # headless OCPP demo (no UI, no hardware)
```

---

## 9. Build workflow — in parts, not one shot

Follow `docs/BUILD_PLAN.md` phase by phase: logic → tests green → wire UI → demoable → next.
**Priority if time is tight:** Pillar A software → OCPP pillar → hardware bridge (mock) →
polish. The hardware bridge is last (client-validated, least certain — §3).

---

## 10. Client & context

Repeat client, good relationship. Two pillars accepted; 7-day delivery. He added the "extract
the bin off the vehicle" step to Pillar A. **Agreed boundary:** we build + fully test the
software; the physical cable-to-vehicle read/write is validated on *his* side, on *his* vehicles
(2014 Charger 3.6, 2015 Silverado 5.3 — real-world targets for later hardware validation). Those
locked ECUs and their uncertain seed/key are why the transport seam exists. No client data is
needed for this build. Full detail in `docs/SCOPE.md`.
