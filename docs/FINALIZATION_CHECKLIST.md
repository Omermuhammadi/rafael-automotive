# FINALIZATION_CHECKLIST.md — Pre-Delivery QA & Hardening

## Purpose
The project is complete and green. This is a **verification and hardening pass, not a build
pass.** The goal is to prove every in-scope requirement works exactly as the spec describes,
harden the unhappy paths, and leave the client zero room for revision requests — **without
destabilizing the working build.**

## Hard rules (read before touching anything)
1. **Default = do NOT modify working code and do NOT add features.** Change code ONLY to fix a
   genuine defect: a failing test, a crash/traceback, a mismatch with the spec, or a broken
   README command. No refactors "for cleanliness," no gold-plating, no new capabilities.
2. **Every code change gets a regression test** that fails before the fix and passes after.
3. **Re-run the FULL suite after every change.** It must stay green.
4. **Scope is unchanged** — two pillars, passive OCPP, neutral byte-patcher. The forbidden
   areas stay forbidden (no CVN-collision / regulatory-evasion, no RSA/bootloader-bypass, no
   OCPP auth-flip forgery). `SCOPE.md` governs.
5. **Stopping condition:** every box below is either ✅ verified-passing or ✅ fixed-with-a-
   regression-test. That is "done." Do **not** invent work beyond this list.

> **RESULT: ✅ COMPLETE.** 1 genuine defect found + fixed with a regression test; 8 additional
> regression tests added for handled-but-untested unhappy paths. Suite grew 94 → **103 tests**,
> deterministically green (ran 4×; only variance is the transient `test_shell` env-skip below).
> One code change total (checksum bounds guard) + tests + docs. Two decisions and one
> lightly-tested caveat are in **Findings for Omer**.

---

## A. Requirements traceability audit  *(highest priority)*
- [x] Re-read BOTH specs fresh — `docs/client_spec.txt` + `docs/client_spec_v2.txt(.txt)`.
- [x] Every in-scope requirement exercised (not trusted). Verification map:
  - Load .bin/.hex/.s19 → `test_binfile` (7). Hex viewer + offsets → `test_hexview` (9) + `test_panel::test_load_populates_hexview_and_identifiers` + screenshot.
  - CALID `1267394012` / CVN `4A8B2C1E` at fixed offsets → `test_identifiers` (7) + `test_panel`.
  - Checksum CRC16/CRC32/block-sum validate + repair (red FAIL → green PASS) → `test_checksum` (7) + `test_panel::test_full_patch_checksum_workflow` + screenshot `e2e_tab1`.
  - Patch-at-offset from external file + original-byte verify → `test_patches` (10).
  - Export `patched_ecu_release.bin` → `test_panel::test_full_patch_checksum_workflow` (export→reload).
  - Extract (`0x10 03`·`0x27`·`0x35`·`0x36`) → `original_ecu_dump.bin`; write-back (`0x10 02`·`0x27`·`0x34`·`0x36`·`0x11 01`) → `test_uds` (12: sequence, roundtrip, ISO-TP wrap, gating) + `test_panel` hardware GUI test.
  - Interface dropdown Mock/Tactrix, Initialize → `test_panel`, `test_uds` (J2534 seam).
  - Mock CSMS on `ws://localhost:9000` (OCPP 1.6J, subprotocol locked) → `test_ocpp` (11).
  - Traffic triage green/orange/red; StartTransaction.conf Invalid → red → `test_scenarios` (11) + `test_ocpp`.
  - Deep-dive JSON audit (offending field) → `test_inspector` (5) + `test_ocpp_panel` + screenshot `e2e_tab3`.
  - Force Remote Station Reset (`Reset.req {"type":"Hard"}`) → `test_scenarios::test_remote_reset_flow` + `test_ocpp_panel`.
  - 8 scenarios headless → ran `scenarios --run all` + `test_scenarios`.
- [x] Every out-of-scope item documented with the agreed reason → `SCOPE.md` (Pillar B/Tab 2, OCPP 2.0.1, map auto-discovery, universal manufacturer-checksum, on-vehicle validation) + traceability matrix.
- [x] Nothing missed/misinterpreted — one interpretation note: spec "Cloud Proxy" = laptop-as-CSMS (built); see Finding #1.
- [x] Updated honest traceability table → `docs/requirements_traceability.html`. **Lightly-tested flag:** the J2534 *real-cable* send/recv/open path is structurally correct but exercised only against the "no driver" path (no hardware here) — validated on the vehicle. Everything else is logic- + GUI-tested.

## B. Spec workflows, walked exactly as written
- [x] **Pillar A v2 Steps 1–4** — init(mock) → extract → load → CALID/CVN → apply → Validate **FAIL(red)** → Patch Checksum **PASS(green, CRC shown)** → export → write-back(mock). Verified by `test_uds` + `test_panel` + screenshot `e2e_tab1`. *(Label-wording note: Finding #2.)*
- [x] **Tab 3 — Simulator mode** — start → colour log → broken scenario red → click red line → JSON deep-dive → Force Reset. Verified by the OCPP/inspector tests + screenshot `e2e_tab3`.
- [⚠] **Tab 3 — Live Proxy mode (forwarding to a separate upstream)** — **NOT in the client spec, not built.** The spec's proxy terminates (laptop *is* the CSMS), which we built. → **Finding #1** (decision, not built).
- [x] **All 8 OCPP scenarios** run headless (`scenarios --run all`) and in-UI with correct colours.

## C. Unhappy paths & robustness  *(no crashes, clear message, test each)*
- [x] Non-bin / wrong-type file → `test_binfile::test_unknown_suffix_loads_raw`, `::test_malformed_hex_raises`.
- [x] Corrupt / truncated / zero-byte bin → `test_binfile::test_zero_byte_bin_loads_empty`, `test_identifiers::test_identifiers_on_empty_or_truncated_buffer_fail_gracefully`.
- [x] Patch offset out of range → `test_patches::test_apply_patch_out_of_range`.
- [x] Patch `original` mismatch → `test_patches::test_apply_patch_original_mismatch_is_rejected_and_buffer_untouched`.
- [x] Malformed / missing patch JSON → `test_patches::test_bad_length_patch_file_raises`, `::test_missing_patch_file_raises`, `::test_invalid_json_patch_file_raises`.
- [x] **Checksum region/stored outside file → DEFECT FOUND & FIXED** (`repair` silently grew the buffer + returned OK). Now returns a clear "Checksum out of range" FAIL, never mutates → `test_checksum::test_out_of_range_region_or_stored_is_rejected_without_corrupting`.
- [x] Proxy unreachable / bad upstream → N/A in our model (no upstream; laptop is the CSMS). Port-in-use handled (`_on_start_error`); running a scenario before start is guarded → `test_ocpp_panel::test_run_scenario_before_start_is_guarded`.
- [x] Charger/CP disconnects mid-session → `test_ocpp::test_abrupt_charge_point_disconnect_is_handled_cleanly` ("disconnected" logged, no crash).
- [x] Buttons out of order / empty states → `test_panel::test_write_before_init_is_guarded`, `::test_apply_disabled_until_file_and_patches`, `test_ocpp_panel::test_run_scenario_before_start_is_guarded`.
- [x] App closes cleanly from every state → verified clean stderr from a loaded + proxy-running + hardware-initialized state (no orphaned threads/tasks).

## D. Test-suite quality & determinism
- [x] The "1 transient env-skip" = `test_shell.py::test_shell_builds_two_tabs`. It **skips** (never fails) when the first `Tk()` of the process can't read Python's `tcl/init.tcl` — an intermittent Windows file lock (AV/indexer) on the Tcl runtime, unrelated to our code. **Not masking a gap:** the identical two-tab window is built + asserted by `test_panel`/`test_ocpp_panel`, which pass in the same run. It is genuinely environmental (some full runs show 0 skips).
- [x] Full suite run **4×** → deterministic: 3× all-pass + a clean run of **103 passed, 0 skipped**. No flaky failures (async included).
- [x] Core logic each has meaningful tests: checksum, patches, UDS sequence + **ISO-TP block-wrap** (`test_large_transfer_reassembles_across_seq_wrap`), seed/key (`test_invalid_key_is_denied`), **NRC/0x78** (`test_response_pending_0x78_is_handled`, `test_excessive_response_pending_gives_up_without_hanging`), triage, and frame capture. *(Our "proxy" is a terminating mock CSMS; the sniffer's frame-capture + triage is what's tested — see Finding #1.)*

## E. Fresh-environment install
- [x] Clean venv → `pip install -r requirements.txt` → app imports, `make_sample`, `scenarios --run all`, and **`python -m src.main` launches** (both tabs, clean stderr).
- [x] `requirements.txt` pins every needed dependency — cross-checked all `src/` third-party imports (bincopy, crccheck, customtkinter, ocpp, websockets); numpy is pulled transitively via `samples.make_sample`. Nothing implicit/missing.
- [x] Every README command works verbatim — `make_sample`, `scenarios --list/--run all/--run invalid_auth`, `pip install`, `run.bat` (`python -m src.main`), and the PyInstaller helper command.
- [x] Every path/filename in the docs is correct (verified all doc-referenced paths exist).

## F. Docs & expectation-setting
- [x] README states the real Tactrix cable **and** real charging station are validated client-side as the expected next step (added the real-station "point the URL at `ws://<laptop-ip>:9000`" note; cable already documented).
- [x] 32-bit runtime note + Tactrix-backend switch instructions are clear and correct.
- [x] `DEMO_CHECKLIST.md` covers both pillars and now **leads with what the client cares about** — added a lead-in foregrounding the OCPP fault triage / payment-transaction bug (the "failure list" he flagged), cross-checked against `docs/client_chat_history.txt`.

---

## Findings for Omer

### Defect found & fixed (with a regression test)
1. **Checksum region/stored outside the loaded file → silent corruption + false success.**
   `checksum.repair()` with a stored-offset past the buffer end silently *grew* the bytearray
   (Python slice-assignment) and returned **OK**. **User-reachable:** load a sub-1 MB image and
   click Patch Checksum — the fixed 1 MB demo layout's region/stored fall outside the buffer, so
   the image silently gains bytes. **Fix:** added `_bounds_error()` guard in `validate`/`repair`
   — both now return a clear `"Checksum out of range"` FAIL and never mutate. **Test:**
   `tests/test_checksum.py::test_out_of_range_region_or_stored_is_rejected_without_corrupting`
   (fails before: buffer 64→68 + OK; passes after: FAIL + buffer unchanged).

### Regression tests added for handled-but-untested unhappy paths (no code change needed)
`test_malformed_hex_raises`, `test_zero_byte_bin_loads_empty`,
`test_identifiers_on_empty_or_truncated_buffer_fail_gracefully`, `test_missing_patch_file_raises`,
`test_invalid_json_patch_file_raises`, `test_abrupt_charge_point_disconnect_is_handled_cleanly`,
`test_run_scenario_before_start_is_guarded`, `test_write_before_init_is_guarded`.

### Decisions for you (noted, NOT built — per the rules)
1. **Section B "Live Proxy mode" (forward sim-CP ↔ a separate mock-upstream) is not built, and is
   not in the client spec.** Both spec PDFs describe the proxy as *"the laptop acts as the
   station's cloud network authority"* — a CSMS that **terminates** the connection and answers,
   which is exactly our Simulator mode (built, verified). There is no separate upstream backend in
   the spec, so "frames forwarded both ways to an upstream" would be **new scope**. The mock-CSMS
   fully satisfies the client's written workflow. **Recommendation:** confirm the mock-CSMS reading
   is acceptable, or commission a forwarding mode as separate work. *(A forwarding relay that
   doesn't alter frames stays within "passive," but it is still new capability — not built.)*
2. **Button-label wording vs the client's doc.** His v2 uses `[ Apply Fix Layout ]`, `[ Validate
   Checksum ]`, `[ Start Local Central System Proxy ]`; the app uses the concise `Apply Fix`,
   `Validate`, `Start Central System Proxy` (functionally identical). Since he'll test against his
   own doc, you may want them verbatim. One word and I'll align them (low-risk text change). **Left
   as-is** — this is a wording preference, not a defect.

### Caveat flagged in the traceability
- **J2534 real-cable I/O is delivered but only lightly testable here** (no cable/vehicle). The
  ctypes seam, ISO15765 setup, registry enumeration, and the no-driver/32-bit error paths are
  tested; the actual PassThru CAN send/recv is validated on the vehicle, per the agreement.

### Scope integrity
- No forbidden work touched or added: no CVN-collision/regulatory-evasion, no RSA/bootloader
  bypass, no OCPP auth-flip forgery. OCPP remains a passive test bench; the patcher stays neutral.
