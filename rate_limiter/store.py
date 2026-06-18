import math
import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Tuple

from .config import Limit


@dataclass(frozen=True)
class Check:
    key: Tuple[str, str, str]
    limit: Limit
    label: str


@dataclass(frozen=True)
class Decision:
    allowed: bool
    limit_label: str = ""
    retry_after_seconds: int = 0
    limit_requests: int = 0
    window_seconds: int = 0


class FixedWindowStore:
    """Thread-safe in-memory fixed-window counter store."""

    def __init__(self, clock=time.time):
        self.clock = clock
        self._lock = threading.Lock()
        self._counters: Dict[Tuple[str, str, str], Tuple[int, float]] = {}

    def check_and_increment(self, checks: List[Check]) -> Decision:
        now = self.clock()

        with self._lock:
            self._prune_expired(now)

            for check in checks:
                count, expires_at = self._current_counter(check.key, check.limit, now)
                if count >= check.limit.requests:
                    return Decision(
                        allowed=False,
                        limit_label=check.label,
                        retry_after_seconds=max(1, math.ceil(expires_at - now)),
                        limit_requests=check.limit.requests,
                        window_seconds=check.limit.window_seconds,
                    )

            for check in checks:
                count, expires_at = self._current_counter(check.key, check.limit, now)
                self._counters[check.key] = (count + 1, expires_at)

        return Decision(allowed=True)

    def size(self) -> int:
        with self._lock:
            self._prune_expired(self.clock())
            return len(self._counters)

    def _current_counter(self, key: Tuple[str, str, str], limit: Limit, now: float) -> Tuple[int, float]:
        count, expires_at = self._counters.get(key, (0, now + limit.window_seconds))
        if expires_at <= now:
            return 0, now + limit.window_seconds
        return count, expires_at

    def _prune_expired(self, now: float) -> None:
        expired_keys = [key for key, (_, expires_at) in self._counters.items() if expires_at <= now]
        for key in expired_keys:
            del self._counters[key]
