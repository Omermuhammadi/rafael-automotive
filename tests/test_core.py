"""Tests for the shared foundation: the Finding model and the thread-safe LogBus."""

from __future__ import annotations

import threading

from src.core.findings import Finding, Severity, Source
from src.core.logbus import LogBus


def test_finding_defaults_and_failure_flag() -> None:
    f = Finding(Source.BINARY, Severity.OK, "Loaded", "1 MiB image loaded")
    assert f.raw is None
    assert isinstance(f.ts, float) and f.ts > 0
    assert f.is_failure is False

    bad = Finding(Source.OCPP, Severity.FAIL, "Rejected", "auth invalid")
    assert bad.is_failure is True


def test_enum_wire_values_are_stable() -> None:
    # These strings are relied on by the UI colour map and any serialisation.
    assert [s.value for s in Severity] == ["ok", "info", "warn", "fail"]
    assert Source.BINARY.value == "binary_studio"
    assert Source.OCPP.value == "ocpp"
    assert Source.HARDWARE.value == "hardware"


def test_logbus_publish_then_drain_dispatches_in_order() -> None:
    bus = LogBus()
    received: list[Finding] = []
    bus.subscribe(received.append)

    first = Finding(Source.OCPP, Severity.INFO, "Authorize", "in flight")
    second = Finding(Source.OCPP, Severity.OK, "Accepted", "token ok")
    bus.publish(first)
    bus.publish(second)

    # Nothing is dispatched until the (UI) thread drains.
    assert received == []

    dispatched = bus.drain()
    assert dispatched == [first, second]
    assert received == [first, second]
    # Draining again with nothing queued is a no-op.
    assert bus.drain() == []


def test_logbus_unsubscribe_stops_delivery() -> None:
    bus = LogBus()
    seen: list[Finding] = []
    unsubscribe = bus.subscribe(seen.append)

    bus.publish(Finding(Source.BINARY, Severity.OK, "a", "a"))
    bus.drain()
    unsubscribe()
    bus.publish(Finding(Source.BINARY, Severity.OK, "b", "b"))
    bus.drain()

    assert [f.title for f in seen] == ["a"]


def test_logbus_publish_is_thread_safe() -> None:
    bus = LogBus()
    seen: list[Finding] = []
    bus.subscribe(seen.append)

    def producer(n: int) -> None:
        for i in range(50):
            bus.publish(Finding(Source.HARDWARE, Severity.INFO, f"{n}-{i}", "x"))

    threads = [threading.Thread(target=producer, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    bus.drain()
    assert len(seen) == 4 * 50
