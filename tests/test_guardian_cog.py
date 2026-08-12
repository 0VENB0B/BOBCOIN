"""Tests for bobcoin.cogs.guardian — the bank-guardian loop logic.

The cog is instantiated without running __init__ (which would start the
30-min tasks loop) and every bank interaction is monkeypatched, so each
branch of _run / _nerf_all / _ai_decide is exercised deterministically:
danger/critical/warning/healthy, AI decisions with -30% clamp, and the
interest-cap writes.
"""

import asyncio

import bobcoin.cogs.guardian as guardian_cog
from bobcoin.cogs.guardian import GuardianCog


def _cog():
    cog = GuardianCog.__new__(GuardianCog)   # skip __init__ (no tasks loop)
    cog.bot = None
    return cog


class _FakeHouseRef:
    def __init__(self):
        self.sets = []

    async def set(self, data, merge=False):
        self.sets.append((data, merge))


class _Recorder:
    """Async call recorder — returns retval (default None) on every call."""

    def __init__(self, retval=None):
        self.calls = []
        self.retval = retval

    async def __call__(self, *a, **kw):
        self.calls.append((a, kw))
        return self.retval


def _lucky(uid, luck, orig=None):
    return {
        "id": str(uid),
        "luck": luck,
        "wallet": 50_000,
        "deposited": 10_000,
        "guardian_original_luck": orig,
    }


def run(coro):
    return asyncio.run(coro)


def _async_return(value):
    """Wrap a value in an async callable (these mocks are awaited)."""
    async def _f(*_a, **_kw):
        return value
    return _f


def _health(**over):
    base = {"status": "healthy", "balance": 20_000_000, "ratio": 0.5}
    base.update(over)
    return base


# ── Static config ──────────────────────────────────────────────────────

def test_interest_cap_mapping():
    assert guardian_cog._INTEREST_CAP == {
        "healthy": 0.30,
        "warning": 0.15,
        "critical": 0.05,
        "danger": 0.00,
    }


# ── _run: status branches ──────────────────────────────────────────────

def test_run_danger_nerfs_half_and_force_collects_15pct(monkeypatch):
    async def scenario():
        cog = _cog()
        house_ref = _FakeHouseRef()
        nerf = _Recorder()
        collect = _Recorder(retval=(2, 1000))

        monkeypatch.setattr(guardian_cog, "_house_ref", lambda: house_ref)
        monkeypatch.setattr(
            guardian_cog, "get_bank_health",
            _async_return(_health(status="danger", balance=100_000, ratio=0.01)),
        )
        monkeypatch.setattr(guardian_cog, "get_lucky_users", _async_return([_lucky(1, 2.0)]))
        monkeypatch.setattr(guardian_cog, "guardian_nerf_user", nerf)
        monkeypatch.setattr(guardian_cog, "guardian_force_collect", collect)

        await cog._run()

        assert nerf.calls == [((1, 2.0, 1.0), {})]          # 2.0 × 0.50
        assert collect.calls == [((), {"pct": 0.15})]
        assert house_ref.sets[0][0] == {
            "guardian_interest_cap": 0.00,
            "guardian_status": "danger",
        }
    run(scenario())


def test_run_critical_nerfs_quarter_and_collects_8pct(monkeypatch):
    async def scenario():
        cog = _cog()
        house_ref = _FakeHouseRef()
        nerf = _Recorder()
        collect = _Recorder(retval=(1, 500))

        monkeypatch.setattr(guardian_cog, "_house_ref", lambda: house_ref)
        monkeypatch.setattr(
            guardian_cog, "get_bank_health",
            _async_return(_health(status="critical", balance=400_000, ratio=0.06)),
        )
        monkeypatch.setattr(guardian_cog, "get_lucky_users", _async_return([_lucky(1, 2.0)]))
        monkeypatch.setattr(guardian_cog, "guardian_nerf_user", nerf)
        monkeypatch.setattr(guardian_cog, "guardian_force_collect", collect)

        await cog._run()

        assert nerf.calls == [((1, 2.0, 1.5), {})]          # 2.0 × 0.75
        assert collect.calls == [((), {"pct": 0.08})]
        assert house_ref.sets[0][0]["guardian_interest_cap"] == 0.05
    run(scenario())


def test_run_warning_asks_ai_and_repays_5pct(monkeypatch):
    async def scenario():
        cog = _cog()
        house_ref = _FakeHouseRef()
        ai_calls = []
        boosted = [_lucky(1, 1.5), _lucky(2, 1.2)]

        async def _ai_decide(health, users):
            ai_calls.append((health, users))

        monkeypatch.setattr(guardian_cog, "_house_ref", lambda: house_ref)
        monkeypatch.setattr(
            guardian_cog, "get_bank_health",
            _async_return(_health(status="warning", balance=1_000_000, ratio=0.15)),
        )
        monkeypatch.setattr(guardian_cog, "get_lucky_users", _async_return(boosted))
        monkeypatch.setattr(cog, "_ai_decide", _ai_decide)
        monkeypatch.setattr(guardian_cog, "get_house_debt", _async_return(500_000))
        repaid = _Recorder(retval=50_000)
        monkeypatch.setattr(guardian_cog, "house_repay_debt", repaid)

        await cog._run()

        assert ai_calls, "AI must decide which users to tune"
        assert ai_calls[0][0]["status"] == "warning"
        assert ai_calls[0][1] == boosted
        assert repaid.calls == [((50_000,), {})]            # 5% of 1M
        assert house_ref.sets[0][0]["guardian_interest_cap"] == 0.15
    run(scenario())


def test_run_warning_no_boosted_skips_ai(monkeypatch):
    async def scenario():
        cog = _cog()

        def _fail(*a, **kw):
            raise AssertionError("_ai_decide must not run without boosted users")

        monkeypatch.setattr(
            guardian_cog, "get_bank_health",
            _async_return(_health(status="warning", balance=1_000_000, ratio=0.15)),
        )
        monkeypatch.setattr(guardian_cog, "get_lucky_users", _async_return([]))
        monkeypatch.setattr(cog, "_ai_decide", _fail)
        monkeypatch.setattr(guardian_cog, "get_house_debt", _async_return(0))
        monkeypatch.setattr(guardian_cog, "house_repay_debt", _Recorder())
        monkeypatch.setattr(
            guardian_cog, "_house_ref", lambda: _FakeHouseRef(),
        )

        await cog._run()
    run(scenario())


def test_run_healthy_restores_and_repays_15pct(monkeypatch):
    async def scenario():
        cog = _cog()
        house_ref = _FakeHouseRef()
        restore = _Recorder(retval=2.0)
        repaid = _Recorder(retval=100_000)

        monkeypatch.setattr(guardian_cog, "_house_ref", lambda: house_ref)
        monkeypatch.setattr(guardian_cog, "get_bank_health", _async_return(_health()))
        monkeypatch.setattr(
            guardian_cog, "get_lucky_users",
            _async_return([_lucky(1, 1.5, orig=2.0)]),    # was nerfed before
        )
        monkeypatch.setattr(guardian_cog, "guardian_restore_user", restore)
        monkeypatch.setattr(guardian_cog, "get_house_debt", _async_return(1_000_000))
        monkeypatch.setattr(guardian_cog, "house_repay_debt", repaid)

        await cog._run()

        assert restore.calls == [((1, 1.5, 2.0), {})]
        assert repaid.calls == [((3_000_000,), {})]        # 15% of 20M
        assert house_ref.sets[0][0]["guardian_interest_cap"] == 0.30
    run(scenario())


def test_run_healthy_no_debt_skips_repay(monkeypatch):
    async def scenario():
        cog = _cog()

        def _fail(*a, **kw):
            raise AssertionError("must not repay when debt is 0")

        monkeypatch.setattr(guardian_cog, "get_bank_health", _async_return(_health()))
        monkeypatch.setattr(guardian_cog, "get_lucky_users", _async_return([]))
        monkeypatch.setattr(guardian_cog, "get_house_debt", _async_return(0))
        monkeypatch.setattr(guardian_cog, "house_repay_debt", _fail)
        monkeypatch.setattr(
            guardian_cog, "_house_ref", lambda: _FakeHouseRef(),
        )

        await cog._run()
    run(scenario())


# ── _nerf_all ──────────────────────────────────────────────────────────

def test_nerf_all_skips_when_factor_is_identity(monkeypatch):
    async def scenario():
        cog = _cog()
        nerf = _Recorder()
        monkeypatch.setattr(guardian_cog, "guardian_nerf_user", nerf)
        await cog._nerf_all([_lucky(1, 2.0), _lucky(2, 1.5)], factor=1.0, label="x")
        assert nerf.calls == []                            # nothing reduced
    run(scenario())


def test_nerf_all_floors_at_1_0(monkeypatch):
    async def scenario():
        cog = _cog()
        nerf = _Recorder()
        monkeypatch.setattr(guardian_cog, "guardian_nerf_user", nerf)
        await cog._nerf_all([_lucky(1, 1.2)], factor=0.5, label="x")
        assert nerf.calls == [((1, 1.2, 1.0), {})]         # max(1.0, 0.6)
    run(scenario())


# ── _ai_decide ─────────────────────────────────────────────────────────

def test_ai_decide_applies_reduction_clamped_to_30pct(monkeypatch):
    async def scenario():
        cog = _cog()
        nerf = _Recorder()

        async def _fake_ai(*_a, **_kw):
            return '{"actions": [{"user_id": "1", "new_luck": 0.1, "reason": "too lucky"}]}'

        monkeypatch.setattr(guardian_cog, "call_ai", _fake_ai)
        monkeypatch.setattr(guardian_cog, "guardian_nerf_user", nerf)
        monkeypatch.setattr(guardian_cog, "get_history", _async_return([]))

        await cog._ai_decide(_health(status="warning"), [_lucky(1, 2.0)])

        # clamp: max(cur*0.70, min(0.1, cur)) = max(1.4, 0.1) = 1.4
        assert nerf.calls == [((1, 2.0, 1.4), {})]
    run(scenario())


def test_ai_decide_never_boosts(monkeypatch):
    async def scenario():
        cog = _cog()
        nerf = _Recorder()

        async def _fake_ai(*_a, **_kw):
            return '{"actions": [{"user_id": "1", "new_luck": 5.0, "reason": "generous"}]}'

        monkeypatch.setattr(guardian_cog, "call_ai", _fake_ai)
        monkeypatch.setattr(guardian_cog, "guardian_nerf_user", nerf)
        monkeypatch.setattr(guardian_cog, "get_history", _async_return([]))

        await cog._ai_decide(_health(status="warning"), [_lucky(1, 2.0)])

        # min(5.0, cur) == cur → new == cur → no nerf (never increases luck)
        assert nerf.calls == []
    run(scenario())


def test_ai_decide_skips_unknown_user_and_bad_json(monkeypatch):
    async def scenario():
        cog = _cog()
        nerf = _Recorder()

        async def _fake_ai(*_a, **_kw):
            return '{"actions": [{"user_id": "999", "new_luck": 0.1}]}'

        monkeypatch.setattr(guardian_cog, "call_ai", _fake_ai)
        monkeypatch.setattr(guardian_cog, "guardian_nerf_user", nerf)
        monkeypatch.setattr(guardian_cog, "get_history", _async_return([]))

        await cog._ai_decide(_health(status="warning"), [_lucky(1, 2.0)])
        assert nerf.calls == []                            # user not boosted

        # garbage JSON → graceful no-op
        async def _garbage(*_a, **_kw):
            return "ไม่ใช่ JSON"
        monkeypatch.setattr(guardian_cog, "call_ai", _garbage)
        await cog._ai_decide(_health(status="warning"), [_lucky(1, 2.0)])
        assert nerf.calls == []
    run(scenario())


def test_ai_decide_empty_actions_is_noop(monkeypatch):
    async def scenario():
        cog = _cog()
        nerf = _Recorder()

        async def _fake_ai(*_a, **_kw):
            return '{"actions": []}'

        monkeypatch.setattr(guardian_cog, "call_ai", _fake_ai)
        monkeypatch.setattr(guardian_cog, "guardian_nerf_user", nerf)
        monkeypatch.setattr(guardian_cog, "get_history", _async_return([]))

        await cog._ai_decide(_health(status="warning"), [_lucky(1, 2.0)])
        assert nerf.calls == []
    run(scenario())


# ── DEV commands (P1 #6: behind GUCOIN_DEV_MODE) ───────────────────────

class _AdminBot:
    async def is_owner(self, user):
        return True


class _Author:
    def __init__(self, uid):
        self.id = uid


class _Ctx:
    def __init__(self, author, bot):
        self.author = author
        self.bot = bot

    async def send(self, *a, **kw):
        pass


def test_guardian_run_and_bankhealth_blocked_without_dev_mode(monkeypatch):
    from conftest import invoke_command
    from discord.ext import commands as _commands

    async def scenario():
        cog = _cog()
        ctx = _Ctx(_Author(1), _AdminBot())
        for command in ("guardian_run", "bankhealth"):
            try:
                await invoke_command(cog, command, ctx)
                raise AssertionError(f"{command} must be blocked when DEV_MODE is off")
            except _commands.CheckFailure:
                pass
    run(scenario())


def test_guardian_run_works_with_dev_mode(monkeypatch):
    from conftest import invoke_command

    import bobcoin.settings as settings

    monkeypatch.setattr(settings, "DEV_MODE", True)

    async def scenario():
        cog = _cog()
        monkeypatch.setattr(guardian_cog, "get_bank_health", _async_return(_health()))
        monkeypatch.setattr(cog, "_run", _async_return(None))
        ctx = _Ctx(_Author(1), _AdminBot())
        await invoke_command(cog, "guardian_run", ctx)
    run(scenario())


def test_metrics_command_blocked_without_dev_mode(monkeypatch):
    from conftest import invoke_command
    from discord.ext import commands as _commands

    async def scenario():
        cog = _cog()
        ctx = _Ctx(_Author(1), _AdminBot())
        try:
            await invoke_command(cog, "metrics", ctx)
            raise AssertionError("$metrics must be blocked when DEV_MODE is off")
        except _commands.CheckFailure:
            pass
    run(scenario())


def test_metrics_command_shows_snapshot(monkeypatch):
    from conftest import invoke_command

    import bobcoin.metrics as metrics
    import bobcoin.settings as settings

    monkeypatch.setattr(settings, "DEV_MODE", True)
    metrics.reset()
    metrics.incr("commands")
    metrics.incr("ai_failures", 2)
    metrics.set_gauge("guilds", 4)

    class _RecCtx:
        def __init__(self, author, bot):
            self.author = author
            self.bot = bot
            self.sent = []

        async def send(self, *a, **kw):
            self.sent.append((a, kw))

    async def scenario():
        cog = _cog()
        ctx = _RecCtx(_Author(1), _AdminBot())
        await invoke_command(cog, "metrics", ctx)
        em = ctx.sent[0][1]["embed"]
        names = [f.name for f in em.fields]
        assert "⏱️ Uptime" in names
        assert "📊 Gauges" in names
        assert "🔢 Counters" in names
        counters = next(f.value for f in em.fields if f.name == "🔢 Counters")
        assert "commands" in counters and "ai_failures" in counters
    run(scenario())
