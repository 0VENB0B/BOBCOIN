"""Tests for bobcoin.metrics — counters, gauges, uptime snapshot."""

import bobcoin.metrics as metrics


def test_incr_and_snapshot():
    metrics.reset()
    metrics.incr("commands")
    metrics.incr("commands")
    metrics.incr("ai_calls", 5)
    snap = metrics.snapshot()
    assert snap["counts"]["commands"] == 2
    assert snap["counts"]["ai_calls"] == 5
    assert snap["uptime_s"] >= 0


def test_gauges():
    metrics.reset()
    metrics.set_gauge("guilds", 3)
    snap = metrics.snapshot()
    assert snap["gauges"]["guilds"] == 3


def test_reset_clears_counts_and_gauges():
    metrics.reset()
    metrics.incr("x")
    metrics.set_gauge("y", 1.0)
    metrics.reset()
    snap = metrics.snapshot()
    assert snap["counts"] == {}
    assert snap["gauges"] == {}
