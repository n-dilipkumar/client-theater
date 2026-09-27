"""Per-token rate limiting for the external content operation.

The add operation is documented as "rate-limited to 1 request per second per
token", so the limit is scoped to the caller's token rather than applied
globally: one seller retrying in a loop must not lock a colleague out of adding
a different file. ``token`` is the caller's identity (the API's ``actor``), and
``None`` is treated as one anonymous bucket rather than as "exempt".

The clock is injected so the rule can be tested without sleeping. Only the add
operation is limited, because that is the operation the research places a limit
on; the re-sync pass in :mod:`dsr.external_library.sync` is a background automation, not
a caller-driven request.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

from dsr.external_library.errors import ExternalSyncError

DEFAULT_MAX_CALLS = 1
DEFAULT_WINDOW_SECONDS = 1.0


class RateLimiter:
    """Allow at most ``max_calls`` per ``window_seconds`` for each token.

    A fixed window rather than a sliding one: the documented guarantee is one
    request per second, and a fixed window is the cheaper shape to reason about
    for a single-process service.
    """

    def __init__(
        self,
        *,
        max_calls: int = DEFAULT_MAX_CALLS,
        window_seconds: float = DEFAULT_WINDOW_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_calls < 1:
            raise ValueError("max_calls must be at least 1")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self.max_calls = max_calls
        self.window_seconds = window_seconds
        self._clock = clock
        self._lock = threading.Lock()
        # token -> (window start, calls already spent in that window)
        self._windows: dict[str, tuple[float, int]] = {}

    def retry_after(self, token: str | None) -> float:
        """Seconds the caller must wait before the next attempt would pass."""
        key = self._key(token)
        with self._lock:
            started, spent = self._windows.get(key, (self._clock(), 0))
            if spent < self.max_calls:
                return 0.0
            return max(0.0, round(started + self.window_seconds - self._clock(), 3))

    def check(self, token: str | None) -> None:
        """Consume a slot for ``token`` or raise ``RateLimitExceeded``."""
        key = self._key(token)
        now = self._clock()
        with self._lock:
            started, spent = self._windows.get(key, (0.0, 0))
            if now - started >= self.window_seconds:
                started, spent = now, 0
            if spent >= self.max_calls:
                wait = max(0.0, round(started + self.window_seconds - now, 3))
                self._windows[key] = (started, spent)
                raise ExternalSyncError(
                    "RateLimitExceeded",
                    f"at most {self.max_calls} request per {self.window_seconds:g}s per token",
                    remediation=(
                        f"Wait {wait:g}s and retry. This operation allows "
                        f"{self.max_calls} request per {self.window_seconds:g}s per token."
                    ),
                    extra={"retry_after_seconds": wait},
                )
            self._windows[key] = (started, spent + 1)

    def reset(self) -> None:
        """Forget every bucket. Used by tests to isolate cases."""
        with self._lock:
            self._windows.clear()

    @staticmethod
    def _key(token: str | None) -> str:
        return token or "anonymous"
