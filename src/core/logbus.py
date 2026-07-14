"""A tiny thread-safe pub/sub so background loops feed the UI without touching Tk.

Both pillars run work off the UI thread: the OCPP server/client are async coroutines on
their own loop, and a UDS transfer streams on a worker thread. Neither may call Tk widgets
directly (Tk is single-threaded). So they :meth:`LogBus.publish` a :class:`Finding` — which
only enqueues, and is safe from any thread — and the UI calls :meth:`LogBus.drain` on a Tk
``.after()`` timer to pop everything queued and dispatch it to subscribers *on the UI thread*.

The bus is deliberately dependency-free and headless-testable: publish then drain, with no
Tk and no event loop required.
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable

from src.core.findings import Finding

Subscriber = Callable[[Finding], None]


class LogBus:
    """Thread-safe fan-out of :class:`Finding`s from producers to UI subscribers.

    Producers (any thread) call :meth:`publish`. The UI thread periodically calls
    :meth:`drain`, which dispatches queued findings to every subscriber in registration
    order. Subscribers therefore always run on whichever thread calls ``drain`` — the UI
    thread in the app — so their callbacks may safely touch widgets.
    """

    def __init__(self) -> None:
        self._queue: queue.Queue[Finding] = queue.Queue()
        self._subscribers: list[Subscriber] = []
        self._lock = threading.Lock()

    def subscribe(self, callback: Subscriber) -> Callable[[], None]:
        """Register ``callback``; returns a zero-arg unsubscribe handle."""
        with self._lock:
            self._subscribers.append(callback)
        return lambda: self.unsubscribe(callback)

    def unsubscribe(self, callback: Subscriber) -> None:
        """Remove ``callback`` if present; a no-op otherwise."""
        with self._lock:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

    def publish(self, finding: Finding) -> None:
        """Enqueue ``finding``. Safe to call from any thread; never touches subscribers."""
        self._queue.put(finding)

    def drain(self) -> list[Finding]:
        """Dispatch all queued findings to subscribers; return what was dispatched.

        Call this on the UI thread (e.g. from a Tk ``.after`` tick). Subscribers are
        snapshotted under the lock so a callback may safely (un)subscribe while draining.
        """
        pending: list[Finding] = []
        while True:
            try:
                pending.append(self._queue.get_nowait())
            except queue.Empty:
                break
        if pending:
            with self._lock:
                subscribers = list(self._subscribers)
            for finding in pending:
                for callback in subscribers:
                    callback(finding)
        return pending

    def pending_count(self) -> int:
        """Approximate number of findings queued but not yet drained."""
        return self._queue.qsize()
