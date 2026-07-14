# Demo Checklist — both pillars, no hardware

A ~5-minute walkthrough that shows everything working with only the bundled sample data and the
software mock backends. No cable, no vehicle, no charger.

> **The headline capability** (the "failure list" you flagged as the detail that was missing):
> the OCPP fault triage in **Section 2** — it pinpoints *exactly* where a charging/payment
> transaction breaks (bad auth, timeout, malformed token, dropped session, rejected boot) and
> shows the offending field in the JSON. **Section 1** is the ICE binary/checksum pillar you asked
> to build first. Lead the demo with whichever the client wants to see; both need zero hardware.

## 0. One-time setup

- [ ] `pip install -r requirements.txt`
- [ ] `python -m samples.make_sample` → writes `samples/synthetic_ecu.bin`
- [ ] `python -m src.main` → a dark, two-tab window opens (**Binary Studio**, **OCPP Sniffer**)

---

## 1. Pillar A — Binary Studio (Tab 1)

**Read & identify**
- [ ] `Load Bin File` → `samples/synthetic_ecu.bin`
- [ ] Hex view fills with offsets; the ASCII column shows `MVDCT-SYNTH-ECU`
- [ ] Sidebar reads **CALID `1267394012`** and **CVN `4A8B2C1E`**

**Patch → checksum → export**
- [ ] `Load Patches` → `samples/example_patches.json`
- [ ] Pick `reserved_region_override` → `Apply Fix` (view jumps to `0x1A2F0`, bytes change)
- [ ] `Validate` → **red: FAIL — checksum mismatch**
- [ ] `Patch Checksum` → **green: PASS** with the new CRC32
- [ ] `Export .bin` → save `patched_ecu_release.bin`

**Vehicle read/write (mock backend — no hardware)**
- [ ] ECU Interface = `Mock ECU` → `Initialize Interface` → "ready — session + security OK"
- [ ] `Extract ECU Binary` → "Extracted 1,048,576 bytes → original_ecu_dump.bin" (loads into view)
- [ ] Optionally patch it, then `Write Binary to ECU` → "Write-back complete — ECU reset"
- [ ] *(Talking point: selecting `Tactrix J2534` uses the same code path on the real cable — see
  the README's 32-bit-Python note. Validated on the vehicle, client side.)*

---

## 2. Pillars C + D — OCPP Sniffer (Tab 3)

- [ ] `Start Central System Proxy` → status: "Listening on ws://localhost:9000 (OCPP 1.6J)"
- [ ] Scenario = `Happy path` → `Run Scenario` → a full **green** session
  (Boot → Authorize → Start → Heartbeat → Stop)
- [ ] Scenario = `Transaction invalid` → `Run Scenario` → `StartTransaction.conf` turns **red**
- [ ] Scenario = `Malformed payload` → `Run Scenario` → a **red** malformed line
- [ ] **Click the red line** → the Frame Inspector shows the JSON with the offending field
  highlighted and a plain-language reason ("the contactor stays open")
- [ ] `Force Remote Station Reset` → `Reset.req {"type":"Hard"}` → the station acks and reboots
- [ ] *(Talking point: run the whole fault library headless — `python -m
  src.ocpp_triage.scenarios --run all`)*

---

## 3. Wrap-up talking points

- [ ] Everything just shown needs **no hardware** — the mock ECU and simulated charge point are
  real components, not stubs.
- [ ] The tool is a **neutral byte-patcher**: patch definitions are the user's own external file;
  nothing vehicle-specific is baked in.
- [ ] The real Tactrix cable and real charging station plug into the **same seams**; that step is
  validated on the client's vehicles.
- [ ] `pytest -q` → the full suite is green.
