"""Tab 1 — Binary Studio panel.

Phase 1 wired load + hex view + identifiers. Phase 2 adds the full patch/checksum workflow:
Load Patch File -> pick patch -> Apply Fix -> Validate Checksum (FAIL, red) -> Patch Checksum
(PASS, green) -> Export patched_ecu_release.bin. All logic lives in the headless modules
(``binfile``/``hexview``/``identifiers``/``patches``/``checksum``); this file only wires them
to widgets. Every action is also driven programmatically via public methods so the flow is
testable without clicking dialogs.
"""

from __future__ import annotations

import tkinter as tk
import threading
from pathlib import Path
from tkinter import filedialog
from tkinter import font as tkfont

import customtkinter as ctk

# Demo defaults = the synthetic fixture's layout. At runtime a user loads their own ECU
# definition + patch file; the repo stays a neutral instrument (SCOPE.md).
from samples.make_sample import LAYOUT as DEMO_LAYOUT
from samples.make_sample import build_sample
from src.app import theme
from src.binary_studio import binfile, checksum, hexview, identifiers, patches
from src.binary_studio.patches import Patch
from src.core.findings import Finding, Severity, Source
from src.core.logbus import LogBus
from src.hardware import uds
from src.hardware.j2534 import J2534Transport, list_j2534_devices
from src.hardware.mock_ecu import MockEcu

BACKEND_MOCK = "Mock ECU"
BACKEND_J2534 = "Tactrix J2534"
_ORIGINAL_DUMP_NAME = "original_ecu_dump.bin"
_FLASH_SIZE = 0x100000  # 1 MiB (spec: 0x000000–0xFFFFFF)

_PATCH_PLACEHOLDER = "— load a patch file —"


class HexView(ctk.CTkFrame):
    """Read-only, virtualised hex dump with an offset gutter and a scrollbar.

    Only ``visible_rows`` lines exist in the underlying Text at any time; scrolling changes
    which slice of the buffer is rendered rather than moving a huge block of text.
    """

    def __init__(self, parent: ctk.CTkBaseClass, width: int = 16) -> None:
        super().__init__(parent, fg_color=theme.BG, corner_radius=6)
        self._buf: bytes = b""
        self._width = width
        self._top_row = 0
        self._visible_rows = 28
        self._font = tkfont.Font(family="Consolas", size=12)
        self._build()

    def _build(self) -> None:
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self._text = tk.Text(
            self,
            wrap="none",
            font=self._font,
            bg=theme.BG,
            fg=theme.TEXT,
            insertbackground=theme.TEXT,
            selectbackground=theme.ACCENT,
            selectforeground="#ffffff",
            borderwidth=0,
            highlightthickness=0,
            padx=10,
            pady=8,
            state="disabled",
            cursor="arrow",
        )
        self._text.grid(row=0, column=0, sticky="nsew")
        self._text.tag_configure("hilite", background="#3a3f4b")

        self._scroll = ctk.CTkScrollbar(self, command=self._on_scrollbar)
        self._scroll.grid(row=0, column=1, sticky="ns", padx=(2, 4), pady=4)

        self._text.bind("<Configure>", self._on_configure)
        for widget in (self, self._text):
            widget.bind("<MouseWheel>", self._on_mousewheel)
        self._text.bind("<Prior>", lambda e: self._scroll_rows(-self._visible_rows))
        self._text.bind("<Next>", lambda e: self._scroll_rows(self._visible_rows))
        self._text.bind("<Up>", lambda e: self._scroll_rows(-1))
        self._text.bind("<Down>", lambda e: self._scroll_rows(1))
        self._text.bind("<Home>", lambda e: self._goto_row(0))
        self._text.bind("<End>", lambda e: self._goto_row(self._max_top_row()))

        self._render_empty()

    # ── public API ──────────────────────────────────────────────────────────
    def set_buffer(self, buf: bytes) -> None:
        self._buf = buf
        self._top_row = 0
        self._refresh()

    def refresh(self) -> None:
        """Re-render the current view (call after the underlying buffer is mutated in place)."""
        self._refresh()

    def clear(self) -> None:
        self._buf = b""
        self._top_row = 0
        self._render_empty()

    def goto_offset(self, offset: int) -> None:
        """Scroll so the row containing ``offset`` is at the top and briefly highlight it."""
        self._goto_row(offset // self._width, highlight=True)

    # ── scrolling ───────────────────────────────────────────────────────────
    def _max_top_row(self) -> int:
        return max(0, hexview.total_rows(self._buf, self._width) - self._visible_rows)

    def _goto_row(self, row: int, highlight: bool = False) -> str:
        self._top_row = max(0, min(self._max_top_row(), row))
        self._refresh(highlight_first=highlight)
        return "break"

    def _scroll_rows(self, delta: int) -> str:
        return self._goto_row(self._top_row + delta)

    def _on_scrollbar(self, action: str, *args: str) -> None:
        total = hexview.total_rows(self._buf, self._width)
        if action == "moveto":
            self._top_row = max(0, min(self._max_top_row(), round(float(args[0]) * total)))
        elif action == "scroll":
            amount, unit = int(args[0]), args[1]
            step = self._visible_rows if unit == "pages" else 1
            self._top_row = max(0, min(self._max_top_row(), self._top_row + amount * step))
        self._refresh()

    def _on_mousewheel(self, event: tk.Event) -> str:
        return self._scroll_rows(-3 if event.delta > 0 else 3)

    def _on_configure(self, event: tk.Event) -> None:
        line_px = self._font.metrics("linespace") or 16
        rows = max(1, (event.height - 16) // line_px)
        if rows != self._visible_rows:
            self._visible_rows = rows
            self._top_row = min(self._top_row, self._max_top_row())
            self._refresh()

    # ── rendering ───────────────────────────────────────────────────────────
    def _render_empty(self) -> None:
        self._set_text("  (load a .bin / .hex / .s19 image to view its bytes)")
        self._scroll.set(0, 1)

    def _refresh(self, highlight_first: bool = False) -> None:
        if not self._buf:
            self._render_empty()
            return
        digits = hexview.offset_digits_for(self._buf)
        view = hexview.rows(self._buf, self._top_row * self._width, self._visible_rows, self._width)
        self._set_text("\n".join(r.render(self._width, digits) for r in view))
        if highlight_first:
            self._text.tag_add("hilite", "1.0", "1.end")

        total = hexview.total_rows(self._buf, self._width)
        if total <= self._visible_rows:
            self._scroll.set(0, 1)
        else:
            self._scroll.set(self._top_row / total, (self._top_row + self._visible_rows) / total)

    def _set_text(self, content: str) -> None:
        self._text.configure(state="normal")
        self._text.delete("1.0", "end")
        self._text.insert("1.0", content)
        self._text.configure(state="disabled")


class BinaryStudioPanel(ctk.CTkFrame):
    """Container for the ICE ECU read / hex / patch / checksum workflow (Pillar A software)."""

    def __init__(self, parent: ctk.CTkBaseClass, logbus: LogBus | None = None) -> None:
        super().__init__(parent, fg_color="transparent")
        self.logbus = logbus
        self._layout = DEMO_LAYOUT
        self._buf: bytearray = bytearray()
        self._loaded_path: Path | None = None
        self._id_rows: list[ctk.CTkLabel] = []
        self._patches: list[Patch] = []
        self._patch_by_id: dict[str, Patch] = {}
        self._transport = None  # active hardware Transport (MockEcu / J2534Transport)
        self._parse_checksum_spec()
        self._build()

    def _parse_checksum_spec(self) -> None:
        cs = self._layout["checksum"]
        self._region = slice(int(cs["region"][0], 0), int(cs["region"][1], 0))
        stored_off = int(cs["stored"], 0)
        self._stored = slice(stored_off, stored_off + int(cs["width"]))
        self._endian = cs.get("endian", "big")
        self._default_algo = cs.get("algo", "crc32")

    # ── construction ──────────────────────────────────────────────────────────
    def _build(self) -> None:
        self.grid_rowconfigure(3, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=0)

        self._build_file_toolbar()      # row 0
        self._build_ecu_bar()           # row 1 — vehicle interface (Phase 5)
        self._build_workflow_toolbar()  # row 2

        self.hexview = HexView(self)
        self.hexview.grid(row=3, column=0, sticky="nsew", padx=(12, 6), pady=(0, 12))

        self._build_sidebar()           # row 3, col 1
        self._set_workflow_enabled(has_file=False, has_patches=False)

    def _build_ecu_bar(self) -> None:
        bar = ctk.CTkFrame(self, fg_color=theme.SURFACE, corner_radius=6)
        bar.grid(row=1, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 6))

        ctk.CTkLabel(bar, text="ECU Interface:", font=("Segoe UI", 12),
                     text_color=theme.TEXT_MUTED).pack(side="left", padx=(12, 6), pady=8)
        # Offer any J2534 devices actually installed on this machine (registry), else the
        # generic Tactrix entry. Empty here — no driver in the build environment.
        self._j2534_devices = dict(list_j2534_devices())
        conn_values = [BACKEND_MOCK] + (list(self._j2534_devices) or [BACKEND_J2534])
        self._conn_menu = ctk.CTkOptionMenu(bar, width=170, values=conn_values,
                                            command=self._on_backend_change)
        self._conn_menu.set(BACKEND_MOCK)
        self._conn_menu.pack(side="left", pady=8)
        self._init_btn = ctk.CTkButton(bar, text="Initialize Interface", width=150,
                                       command=self._on_initialize)
        self._init_btn.pack(side="left", padx=6, pady=8)
        self._extract_btn = ctk.CTkButton(bar, text="Extract ECU Binary", width=150,
                                          command=self._on_extract, state="disabled")
        self._extract_btn.pack(side="left", padx=6, pady=8)
        self._write_btn = ctk.CTkButton(bar, text="Write Binary to ECU", width=160,
                                        command=self._on_write, state="disabled")
        self._write_btn.pack(side="left", padx=6, pady=8)

        self._hw_status = ctk.CTkLabel(bar, text="Mock backend — no hardware needed.",
                                       font=("Segoe UI", 12), text_color=theme.TEXT_MUTED)
        self._hw_status.pack(side="left", padx=12, pady=8)

    def _build_file_toolbar(self) -> None:
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=0, column=0, columnspan=2, sticky="ew", padx=12, pady=(12, 6))

        ctk.CTkButton(bar, text="Load Bin File", width=120, command=self._on_load).pack(side="left")
        self._status = ctk.CTkLabel(
            bar, text="No file loaded.", font=("Segoe UI", 12), text_color=theme.TEXT_MUTED
        )
        self._status.pack(side="left", padx=14)

        ctk.CTkButton(bar, text="Go", width=44, command=self._on_goto).pack(side="right")
        self._goto_entry = ctk.CTkEntry(bar, width=120, placeholder_text="offset e.g. 0x20")
        self._goto_entry.pack(side="right", padx=(0, 8))
        self._goto_entry.bind("<Return>", lambda e: self._on_goto())
        ctk.CTkLabel(bar, text="Go to:", font=("Segoe UI", 12), text_color=theme.TEXT_MUTED).pack(
            side="right", padx=(0, 6)
        )

    def _build_workflow_toolbar(self) -> None:
        bar = ctk.CTkFrame(self, fg_color=theme.SURFACE, corner_radius=6)
        bar.grid(row=2, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 10))

        # Patch group (left)
        ctk.CTkButton(bar, text="Load Patches", width=110, command=self._on_load_patches).pack(
            side="left", padx=(10, 6), pady=8
        )
        self._patch_menu = ctk.CTkOptionMenu(bar, width=210, values=[_PATCH_PLACEHOLDER],
                                             command=self._on_patch_selected)
        self._patch_menu.set(_PATCH_PLACEHOLDER)
        self._patch_menu.pack(side="left", padx=6, pady=8)
        self._apply_btn = ctk.CTkButton(bar, text="Apply Fix Layout", width=130,
                                        command=self._on_apply_patch)
        self._apply_btn.pack(side="left", padx=6, pady=8)

        # Checksum + export group (right)
        self._export_btn = ctk.CTkButton(bar, text="Export .bin", width=100, command=self._on_export)
        self._export_btn.pack(side="right", padx=(6, 10), pady=8)
        self._repair_btn = ctk.CTkButton(bar, text="Patch Checksum", width=130,
                                         command=self._on_patch_checksum)
        self._repair_btn.pack(side="right", padx=6, pady=8)
        self._validate_btn = ctk.CTkButton(bar, text="Validate Checksum", width=140,
                                           command=self._on_validate_checksum)
        self._validate_btn.pack(side="right", padx=6, pady=8)
        self._algo_menu = ctk.CTkOptionMenu(bar, width=100,
                                            values=list(checksum.SUPPORTED_ALGOS))
        self._algo_menu.set(self._default_algo)
        self._algo_menu.pack(side="right", padx=6, pady=8)
        ctk.CTkLabel(bar, text="Checksum:", font=("Segoe UI", 12), text_color=theme.TEXT_MUTED).pack(
            side="right", padx=(10, 4)
        )

    def _build_sidebar(self) -> None:
        side = ctk.CTkFrame(self, fg_color=theme.SURFACE, corner_radius=6, width=300)
        side.grid(row=3, column=1, sticky="nsew", padx=(6, 12), pady=(0, 12))
        side.grid_propagate(False)

        ctk.CTkLabel(side, text="Calibration Identifiers", font=("Segoe UI", 14, "bold"),
                     text_color=theme.TEXT).pack(anchor="w", padx=14, pady=(14, 2))
        ctk.CTkLabel(side, text="Read at fixed offsets from the loaded layout.",
                     font=("Segoe UI", 11), text_color=theme.TEXT_MUTED,
                     wraplength=260, justify="left").pack(anchor="w", padx=14, pady=(0, 8))
        self._id_container = ctk.CTkFrame(side, fg_color="transparent")
        self._id_container.pack(fill="x", padx=8)

        ctk.CTkLabel(side, text="Checksum", font=("Segoe UI", 14, "bold"),
                     text_color=theme.TEXT).pack(anchor="w", padx=14, pady=(18, 2))
        self._cksum_status = ctk.CTkLabel(side, text="— not checked —", font=theme.MONO_FONT,
                                          text_color=theme.TEXT_MUTED, justify="left", anchor="w",
                                          wraplength=270)
        self._cksum_status.pack(anchor="w", fill="x", padx=14)

        ctk.CTkLabel(side, text="Image", font=("Segoe UI", 14, "bold"),
                     text_color=theme.TEXT).pack(anchor="w", padx=14, pady=(18, 2))
        self._image_info = ctk.CTkLabel(side, text="—", font=theme.MONO_FONT,
                                        text_color=theme.TEXT_MUTED, justify="left", anchor="w")
        self._image_info.pack(anchor="w", fill="x", padx=14)

        self._render_identifiers([])

    # ── file actions ──────────────────────────────────────────────────────────
    def _on_load(self) -> None:
        path = filedialog.askopenfilename(
            title="Load ECU binary",
            filetypes=[("ECU images", "*.bin *.hex *.s19 *.s28 *.s37 *.srec *.rom"),
                       ("All files", "*.*")],
        )
        if path:
            self.load_path(path)

    def load_path(self, path: str | Path) -> bool:
        """Load ``path`` into the view + identifier block. Returns True on success."""
        path = Path(path)
        try:
            buf = binfile.load(path)
        except Exception as exc:
            self._set_status(f"Failed to load {path.name}: {exc}", error=True)
            self._publish(Finding(Source.BINARY, Severity.FAIL, "Load failed", f"{path}: {exc}"))
            return False
        self._loaded_path = path
        self._adopt_buffer(buf, f"{path.name}  •  {len(buf):,} bytes  •  {self._format_label(path)}")
        self._publish(Finding(Source.BINARY, Severity.OK, f"Loaded {path.name}", f"{len(buf):,} bytes"))
        return True

    def _adopt_buffer(self, buf: bytes, status: str) -> None:
        """Make ``buf`` the working image (shared by Load Bin File and Extract ECU Binary)."""
        self._buf = bytearray(buf)
        self.hexview.set_buffer(self._buf)
        self._set_status(status)
        self._image_info.configure(
            text=f"size : {len(buf):,} bytes\n"
                 f"range: 0x0 – 0x{max(len(buf) - 1, 0):X}\n"
                 f"rows : {hexview.total_rows(buf):,}"
        )
        self._set_cksum_status("— not checked —", None)
        findings = identifiers.read_identifiers(self._buf, self._layout)
        self._render_identifiers(findings)
        for f in findings:
            self._publish(f)
        self._set_workflow_enabled(has_file=True, has_patches=bool(self._patches))
        if self._transport is not None:
            self._write_btn.configure(state="normal")

    # ── vehicle interface (Phase 5) ───────────────────────────────────────────
    def _make_transport(self, backend: str):
        if backend == BACKEND_MOCK:
            # Mock ECU seeded with the synthetic image, so Extract yields a realistic dump.
            return MockEcu(image=bytes(build_sample()))
        # A specific enumerated device (use its DLL) or the generic Tactrix entry (auto-discover).
        return J2534Transport(dll_path=self._j2534_devices.get(backend))

    def _on_backend_change(self, backend: str) -> None:
        self._close_transport()
        self._extract_btn.configure(state="disabled")
        if backend == BACKEND_MOCK:
            self._set_hw_status("Mock backend — no hardware needed.")
        else:
            self._set_hw_status(f"{backend}: validate on the vehicle (32-bit Python + cable).")

    def _on_initialize(self) -> None:
        backend = self._conn_menu.get()
        self._close_transport()
        self._init_btn.configure(state="disabled")
        self._set_hw_status(f"Initializing {backend}…")

        def work():
            transport = self._make_transport(backend)
            transport.open()
            if not uds.initialize(transport, publish=self._publish):
                transport.close()
                raise RuntimeError("session/security handshake failed")
            return transport

        self._run_bg(work, self._on_initialized, self._on_hw_error(self._init_btn))

    def _on_initialized(self, transport) -> None:
        self._transport = transport
        self._init_btn.configure(state="normal")
        self._extract_btn.configure(state="normal")
        if self._buf:
            self._write_btn.configure(state="normal")
        self._set_hw_status(f"{self._conn_menu.get()} ready — session + security OK.", Severity.OK)

    def _on_extract(self) -> None:
        if self._transport is None:
            return
        transport = self._transport
        self._extract_btn.configure(state="disabled")
        self._set_hw_status("Extracting ECU binary (0x35 / 0x36)…")

        def work():
            return uds.extract_binary(transport, 0, _FLASH_SIZE, publish=self._publish)

        self._run_bg(work, self._on_extracted, self._on_hw_error(self._extract_btn))

    def _on_extracted(self, data) -> None:
        self._extract_btn.configure(state="normal")
        if not data:
            self._set_hw_status("Extract failed — see the log.", Severity.FAIL)
            return
        out = Path(_ORIGINAL_DUMP_NAME)
        try:
            binfile.save(out, data)
        except Exception as exc:
            self._set_hw_status(f"Extracted but could not save: {exc}", Severity.FAIL)
        self._adopt_buffer(data, f"Extracted {len(data):,} bytes  •  {out.name}")
        self._set_hw_status(f"Extracted {len(data):,} bytes → {out.name}.", Severity.OK)

    def _on_write(self) -> None:
        if self._transport is None or not self._buf:
            self._set_hw_status("Initialize the interface and load/extract a binary first.",
                                Severity.FAIL)
            return
        transport = self._transport
        data = bytes(self._buf)
        self._write_btn.configure(state="disabled")
        self._set_hw_status("Writing binary to ECU (0x34 / 0x36 / 0x11)…")

        def work():
            return uds.write_binary(transport, 0, data, publish=self._publish)

        self._run_bg(work, self._on_written, self._on_hw_error(self._write_btn))

    def _on_written(self, ok) -> None:
        self._write_btn.configure(state="normal")
        if ok:
            self._set_hw_status("Write-back complete — ECU reset. Vehicle patched.", Severity.OK)
        else:
            self._set_hw_status("Write-back failed — see the log.", Severity.FAIL)

    def _close_transport(self) -> None:
        if self._transport is not None:
            try:
                self._transport.close()
            except Exception:
                pass
            self._transport = None

    def _run_bg(self, fn, on_ok, on_err) -> None:
        """Run a (possibly blocking) hardware op on a worker thread; dispatch back on the UI thread."""
        holder: dict = {}

        def worker() -> None:
            try:
                holder["ok"] = fn()
            except Exception as exc:  # surface hardware errors to the technician
                holder["err"] = exc

        thread = threading.Thread(target=worker, name="ecu-io", daemon=True)
        thread.start()

        def poll() -> None:
            if thread.is_alive():
                self.after(60, poll)
                return
            if "err" in holder:
                on_err(holder["err"])
            else:
                on_ok(holder["ok"])
        self.after(60, poll)

    def _on_hw_error(self, button: ctk.CTkButton):
        def handler(exc: Exception) -> None:
            button.configure(state="normal")
            self._set_hw_status(str(exc), Severity.FAIL)
        return handler

    def _set_hw_status(self, text: str, severity: Severity | None = None) -> None:
        color = theme.color_for(severity) if severity else theme.TEXT_MUTED
        self._hw_status.configure(text=text, text_color=color)

    def _on_goto(self) -> None:
        text = self._goto_entry.get().strip()
        if not text:
            return
        try:
            offset = int(text, 0)
        except ValueError:
            self._set_status(f"'{text}' is not a valid offset (try 0x20 or 32).", error=True)
            return
        if not self._buf:
            self._set_status("Load a file before navigating.", error=True)
            return
        if not 0 <= offset < len(self._buf):
            self._set_status(f"Offset {offset:#x} is outside the image (0..{len(self._buf) - 1:#x}).",
                             error=True)
            return
        self.hexview.goto_offset(offset)
        self._set_status(f"Jumped to {offset:#x}.")

    # ── patch actions ─────────────────────────────────────────────────────────
    def _on_load_patches(self) -> None:
        path = filedialog.askopenfilename(
            title="Load patch definitions",
            filetypes=[("Patch definitions", "*.json"), ("All files", "*.*")],
        )
        if path:
            self.load_patches(path)

    def load_patches(self, path: str | Path) -> bool:
        """Load patch definitions and populate the dropdown. Returns True on success."""
        path = Path(path)
        try:
            defs = patches.load_patch_defs(path)
        except Exception as exc:
            self._set_status(f"Failed to load patches: {exc}", error=True)
            self._publish(Finding(Source.BINARY, Severity.FAIL, "Patch file error", f"{path}: {exc}"))
            return False
        if not defs:
            self._set_status(f"{path.name} contains no patches.", error=True)
            return False

        self._patches = defs
        self._patch_by_id = {p.id: p for p in defs}
        self._patch_menu.configure(values=[p.id for p in defs])
        self._patch_menu.set(defs[0].id)
        self._on_patch_selected(defs[0].id)
        self._set_status(f"Loaded {len(defs)} patch(es) from {path.name}.")
        self._set_workflow_enabled(has_file=bool(self._buf), has_patches=True)
        return True

    def _on_patch_selected(self, patch_id: str) -> None:
        patch = self._patch_by_id.get(patch_id)
        if patch and patch.description:
            self._set_status(f"{patch.id}: {patch.description}")

    def apply_selected_patch(self) -> bool:
        """Apply the currently selected patch to the loaded image. Returns True on success."""
        if not self._buf:
            self._set_status("Load a binary before applying a patch.", error=True)
            return False
        patch = self._patch_by_id.get(self._patch_menu.get())
        if patch is None:
            self._set_status("Select a patch first.", error=True)
            return False

        finding = patches.apply_patch(self._buf, patch)
        self._publish(finding)
        if finding.is_failure:
            self._set_status(finding.detail, error=True)
            return False

        self.hexview.goto_offset(patch.offset)
        self._set_status(finding.detail)
        self._set_cksum_status("modified — re-validate", Severity.WARN)
        return True

    def _on_apply_patch(self) -> None:
        self.apply_selected_patch()

    # ── checksum actions ──────────────────────────────────────────────────────
    def validate_checksum(self) -> Finding | None:
        if not self._buf:
            self._set_status("Load a binary first.", error=True)
            return None
        algo = self._algo_menu.get()
        finding = checksum.validate(self._buf, self._region, self._stored, algo, self._endian)
        self._publish(finding)
        if finding.is_failure:
            self._set_cksum_status(f"FAIL  {algo.upper()}  mismatch", Severity.FAIL)
            self._set_status("Checksum mismatch — patch the checksum before flashing.", error=True)
        else:
            self._set_cksum_status(f"PASS  {algo.upper()}  {finding.raw}", Severity.OK)
            self._set_status("Checksum valid.")
        return finding

    def _on_validate_checksum(self) -> None:
        self.validate_checksum()

    def patch_checksum(self) -> Finding | None:
        if not self._buf:
            self._set_status("Load a binary first.", error=True)
            return None
        algo = self._algo_menu.get()
        finding = checksum.repair(self._buf, self._region, self._stored, algo, self._endian)
        self._publish(finding)
        self.hexview.goto_offset(self._stored.start)
        self._set_cksum_status(f"PASS  {algo.upper()}  {finding.raw}", Severity.OK)
        self._set_status(finding.detail)
        return finding

    def _on_patch_checksum(self) -> None:
        self.patch_checksum()

    def _on_export(self) -> None:
        if not self._buf:
            self._set_status("Load a binary first.", error=True)
            return
        path = filedialog.asksaveasfilename(
            title="Export patched binary", defaultextension=".bin",
            initialfile="patched_ecu_release.bin",
            filetypes=[("Raw binary", "*.bin"), ("Intel HEX", "*.hex"),
                       ("S-record", "*.s19"), ("All files", "*.*")],
        )
        if path:
            self.export_to(path)

    def export_to(self, path: str | Path) -> bool:
        """Write the (patched) image to ``path``. Returns True on success."""
        path = Path(path)
        try:
            binfile.save(path, self._buf)
        except Exception as exc:
            self._set_status(f"Export failed: {exc}", error=True)
            return False
        self._set_status(f"Exported {path.name}  ({len(self._buf):,} bytes).")
        self._publish(Finding(Source.BINARY, Severity.OK, f"Exported {path.name}",
                              f"{len(self._buf):,} bytes"))
        return True

    # ── rendering helpers ─────────────────────────────────────────────────────
    def _render_identifiers(self, findings: list[Finding]) -> None:
        for row in self._id_rows:
            row.destroy()
        self._id_rows = []

        if not findings:
            lbl = ctk.CTkLabel(self._id_container, text="— no file loaded —",
                               font=("Segoe UI", 12), text_color=theme.TEXT_MUTED)
            lbl.pack(anchor="w", padx=6, pady=4)
            self._id_rows = [lbl]
            return

        for f in findings:
            lbl = ctk.CTkLabel(self._id_container, text=f.title, font=theme.MONO_FONT,
                               text_color=theme.color_for(f.severity), anchor="w", justify="left")
            lbl.pack(anchor="w", fill="x", padx=6, pady=3)
            self._id_rows.append(lbl)

    def _set_cksum_status(self, text: str, severity: Severity | None) -> None:
        color = theme.color_for(severity) if severity else theme.TEXT_MUTED
        self._cksum_status.configure(text=text, text_color=color)

    def _set_status(self, text: str, error: bool = False) -> None:
        self._status.configure(
            text=text,
            text_color=theme.SEVERITY_COLORS[Severity.FAIL] if error else theme.TEXT_MUTED,
        )

    def _set_workflow_enabled(self, has_file: bool, has_patches: bool) -> None:
        file_state = "normal" if has_file else "disabled"
        for btn in (self._validate_btn, self._repair_btn, self._export_btn):
            btn.configure(state=file_state)
        self._apply_btn.configure(state="normal" if (has_file and has_patches) else "disabled")

    def _publish(self, finding: Finding) -> None:
        if self.logbus:
            self.logbus.publish(finding)

    @staticmethod
    def _format_label(path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix in binfile.CONTAINER_SUFFIXES:
            return "Intel HEX" if suffix in {".hex", ".ihex"} else "S-record"
        return "raw binary"
