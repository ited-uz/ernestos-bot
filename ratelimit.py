"""
ErnestOS — request rate limiting.

One process, one dictionary. That is the right shape for a single instance and
the wrong shape for several, so the decision lives behind a small interface:
`RateLimiter` says what a limiter must be able to answer, and
`InMemoryRateLimiter` is the answer this deployment uses. Swapping in a Redis
implementation later means writing one class and changing one line in `app.py`
— not hunting for a dictionary that forty call sites reach into.

The leak this module exists to fix
----------------------------------

The original limiter pruned expired *timestamps* inside a bucket but never
removed the bucket itself. Every distinct (identity, class) pair got a list
that lived as long as the process, and unauthenticated callers are bucketed by
a hash of their client host — so the number of buckets grew with the number of
addresses that ever touched the API, and nothing ever shrank it. On a
long-running deploy that is a slow leak with no ceiling.

`InMemoryRateLimiter` sweeps: a bucket whose newest hit is older than its own
window holds nothing but expired entries, so it can be dropped whole. The sweep
runs on a timer rather than on every request, because walking the whole
dictionary per call would trade a memory leak for a CPU one.
"""

from __future__ import annotations

import time
from typing import Protocol


class RateLimiter(Protocol):
    """What every limiter must answer.

    `check` is one question — "may this caller spend one request in this
    bucket?" — and it both decides and records. Returning the wait in seconds
    rather than a bare False is what lets the caller send a truthful
    `Retry-After` instead of a guess.
    """

    def check(self, key: int, bucket: str) -> int | None:
        """Seconds to wait when over budget, else None (and the hit is spent)."""
        ...

    def reset(self) -> None:
        """Forget everything. Tests use this; production never calls it."""
        ...


class InMemoryRateLimiter:
    """Token buckets in a plain dict, swept so they cannot accumulate.

    `limits` maps a bucket name to (allowance, window_seconds). Reads are
    cheap, writes cost more and exports hit Telegram, which is why each class
    carries its own budget rather than sharing one.
    """

    def __init__(self, limits: dict[str, tuple[int, int]], *,
                 sweep_every: int = 300, clock=time.monotonic) -> None:
        self.limits = limits
        self.sweep_every = sweep_every
        self._clock = clock
        self._hits: dict[tuple[int, str], list[float]] = {}
        self._swept_at = clock()

    # -- the decision ------------------------------------------------------

    def check(self, key: int, bucket: str) -> int | None:
        limit, window = self.limits[bucket]
        now = self._clock()
        self._maybe_sweep(now)

        hits = self._hits.setdefault((key, bucket), [])
        cutoff = now - window
        hits[:] = [h for h in hits if h > cutoff]
        if len(hits) >= limit:
            return max(1, int(hits[0] + window - now))
        hits.append(now)
        return None

    def reset(self) -> None:
        self._hits.clear()
        self._swept_at = self._clock()

    # -- housekeeping ------------------------------------------------------

    def _maybe_sweep(self, now: float) -> None:
        if now - self._swept_at < self.sweep_every:
            return
        self._swept_at = now
        self.sweep(now)

    def sweep(self, now: float | None = None) -> int:
        """Drop buckets holding nothing but expired hits. Returns how many.

        A bucket is only removed when its *newest* hit has aged past that
        bucket's own window — at which point the bucket is empty by definition,
        so dropping it cannot forgive a request somebody still owes.
        """
        now = self._clock() if now is None else now
        dead = [
            k for k, hits in self._hits.items()
            if not hits or hits[-1] <= now - self.limits[k[1]][1]
        ]
        for k in dead:
            del self._hits[k]
        return len(dead)

    @property
    def buckets(self) -> int:
        """How many buckets are being tracked. For tests and diagnostics."""
        return len(self._hits)
