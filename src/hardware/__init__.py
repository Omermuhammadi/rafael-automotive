"""Pillar A hardware bridge (Phase 5).

The UDS sequence sits behind a swappable ``Transport`` seam: a pure-software ``mock_ecu``
for build-time validation, and a ``j2534`` ctypes backend the client validates on a real
vehicle. Default everywhere to the mock (CLAUDE.md Section 3).
"""
