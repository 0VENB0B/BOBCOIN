"""Lightweight in-process metrics for ops visibility (P3).

Plain dict counters/gauges with a monotonic uptime clock — no external deps,
no threads (single event loop), cheap enough to call on every command/game.
``snapshot()`` returns a plain dict that ``$metrics`` renders.
"""

import time

_started = time.monotonic()
_counts: dict[str, int] = {}
_gauges: dict[str, float] = {}


def incr(name: str, by: int = 1) -> None:
    _counts[name] = _counts.get(name, 0) + by


def set_gauge(name: str, value: float) -> None:
    _gauges[name] = value


def snapshot() -> dict:
    return {
        "uptime_s": int(time.monotonic() - _started),
        "counts": dict(sorted(_counts.items())),
        "gauges": dict(sorted(_gauges.items())),
    }


def reset() -> None:
    """Clear counters/gauges (tests only). Uptime keeps running."""
    _counts.clear()
    _gauges.clear()
