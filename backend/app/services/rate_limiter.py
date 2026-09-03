"""
Rate limiting service for proposal generation.

Uses an in-memory sliding-window log to protect LLM endpoints against
abuse, runaway agent retry loops, and rapid-fire automated scraping.
Zero external infrastructure required for demo/testing.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict


class InMemoryRateLimiter:
    """Thread-safe sliding-window rate limiter."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._timestamps: dict[str, list[float]] = defaultdict(list)

    def is_allowed(
        self,
        key: str,
        max_requests: int,
        window_seconds: float = 60.0,
        now: float | None = None,
    ) -> tuple[bool, int]:
        """
        Check whether `key` has exceeded `max_requests` within `window_seconds`.

        Returns:
            (allowed: bool, retry_after_seconds: int)
        """
        if max_requests <= 0:
            return True, 0

        current_time = now if now is not None else time.time()
        cutoff = current_time - window_seconds

        with self._lock:
            history = self._timestamps[key]
            valid_history = [t for t in history if t > cutoff]
            self._timestamps[key] = valid_history

            if len(valid_history) >= max_requests:
                earliest = valid_history[0]
                retry_after = max(1, int(window_seconds - (current_time - earliest)))
                return False, retry_after

            valid_history.append(current_time)
            return True, 0

    def reset(self, key: str | None = None) -> None:
        """Clear recorded timestamps for test isolation."""
        with self._lock:
            if key:
                self._timestamps.pop(key, None)
            else:
                self._timestamps.clear()


limiter = InMemoryRateLimiter()
