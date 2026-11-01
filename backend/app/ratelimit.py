"""In-memory sliding-window limits for account endpoints (login failures, new guests,
new accounts).

State lives in this process, which is exact for the single-process deployment. Grading
limits do not use this: they count rows in the database, so they hold across processes.
"""

import threading
import time
from collections import defaultdict, deque
from datetime import timedelta


class SlidingWindowLimiter:
    def __init__(self, limit: tuple[int, timedelta]):
        self.max_events, window = limit
        self.window_seconds = window.total_seconds()
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _recent(self, key: str, now: float) -> deque[float]:
        events = self._events[key]
        while events and events[0] <= now - self.window_seconds:
            events.popleft()
        if not events:
            # Drop empty keys so one-off keys (e.g. mistyped emails) do not pile up.
            del self._events[key]
            return deque()
        return events

    def is_limited(self, key: str = "") -> bool:
        with self._lock:
            return len(self._recent(key, time.monotonic())) >= self.max_events

    def hit(self, key: str = "") -> None:
        with self._lock:
            now = time.monotonic()
            self._recent(key, now)
            self._events[key].append(now)

    def try_hit(self, key: str = "") -> bool:
        """Record an event if the limit allows it. Returns False when limited."""
        with self._lock:
            now = time.monotonic()
            if len(self._recent(key, now)) >= self.max_events:
                return False
            self._events[key].append(now)
            return True

    def clear(self) -> None:
        with self._lock:
            self._events.clear()
