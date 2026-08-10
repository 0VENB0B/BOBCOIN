"""Pytest bootstrap: make the project importable and fake out Firestore."""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fake_firestore import install_fake_firestore  # noqa: E402

install_fake_firestore()


@pytest.fixture(autouse=True)
def fresh_store():
    """Reset the fake Firestore + TTL cache before every test.

    Yields the underlying store dict so tests can reach in and mutate
    ``system/bank`` etc. directly to simulate unusual house states.
    """
    import bobcoin.bank.core as bank_core
    from fake_firestore import FakeClient

    store = {}
    bank_core._db = FakeClient(store)
    bank_core._cache.clear()
    yield store


async def invoke_command(cog, command_name, ctx, *args, **kwargs):
    """Invoke a ``@commands.command`` directly against a fake ctx.

    discord.py 2.7 Commands are plain descriptors (``cog.cmd`` returns the
    Command object, not a bound method) and ``__call__`` requires the Command
    to be bound to its cog — which normally happens in ``bot.add_cog``. This
    helper replicates that binding, then runs the registered check predicates
    the same way live dispatch does (sync and async both, via
    ``maybe_coroutine``), letting checks raise ``MissingPermissions`` /
    ``MissingAnyRole`` etc. naturally.

    ``*args``/``**kwargs`` are forwarded to the callback (keyword-only
    command params like ``DTC(*, text=...)`` need ``text=...`` kwargs).

    NOTE: like ``Command.__call__`` this bypasses ``cog_before_invoke``
    (e.g. economy's NotRegistered guard) and ``@commands.cooldown`` buckets
    — set up the state the command itself guards against explicitly.
    """
    import discord.utils
    from discord.ext import commands as _commands

    cmd = getattr(cog, command_name)
    cmd.cog = cog
    for check in cmd.checks:
        if not await discord.utils.maybe_coroutine(check, ctx):
            raise _commands.CheckFailure(f"check failed for {command_name}")
    return await cmd.callback(cog, ctx, *args, **kwargs)
