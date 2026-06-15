import asyncio
import json
import logging
import os

from .settings import BANK_FILE

logger = logging.getLogger("bobcoin.bank")
_BANK_LOCK = asyncio.Lock()


def _read_bank_unlocked():
    if not BANK_FILE.exists():
        return {}

    try:
        with BANK_FILE.open("r", encoding="utf-8") as f:
            users = json.load(f)
    except (OSError, json.JSONDecodeError):
        logger.exception("Failed to read bank data")
        return {}

    if not isinstance(users, dict):
        return {}
    return users


def _write_bank_unlocked(users):
    tmp_file = BANK_FILE.with_suffix(".json.tmp")
    with tmp_file.open("w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, separators=(",", ":"))
        f.write("\n")
    os.replace(tmp_file, BANK_FILE)


def _account(users, user_id):
    account = users.setdefault(str(user_id), {})
    account["wallet"] = int(account.get("wallet", 0))
    account["bank"] = int(account.get("bank", 0))
    return account


async def open_account(user):
    async with _BANK_LOCK:
        users = _read_bank_unlocked()
        if str(user.id) in users:
            before = dict(users[str(user.id)])
            _account(users, user.id)
            if users[str(user.id)] != before:
                _write_bank_unlocked(users)
            return False

        users[str(user.id)] = {"wallet": 0, "bank": 0}
        _write_bank_unlocked(users)
        return True


async def get_bank_data():
    async with _BANK_LOCK:
        return _read_bank_unlocked()


async def get_balance(user):
    async with _BANK_LOCK:
        users = _read_bank_unlocked()
        account = users.get(str(user.id))
        if account is None:
            account = {"wallet": 0, "bank": 0}
            users[str(user.id)] = account
            _write_bank_unlocked(users)
        else:
            before = dict(account)
            _account(users, user.id)
            if account != before:
                _write_bank_unlocked(users)
        return [account["wallet"], account["bank"]]


async def update_bank(user, change=0, mode="wallet"):
    if mode not in {"wallet", "bank"}:
        raise ValueError("mode must be wallet or bank")

    async with _BANK_LOCK:
        users = _read_bank_unlocked()
        account = _account(users, user.id)
        change = int(change)
        new_balance = account[mode] + change
        if new_balance < 0:
            return None
        if change == 0:
            return [account["wallet"], account["bank"]]
        account[mode] = new_balance
        _write_bank_unlocked(users)
        return [account["wallet"], account["bank"]]


async def transfer_funds(user, amount, source, target):
    async with _BANK_LOCK:
        users = _read_bank_unlocked()
        account = _account(users, user.id)
        if account[source] < amount:
            return None
        account[source] -= amount
        account[target] += amount
        _write_bank_unlocked(users)
        return [account["wallet"], account["bank"]]


async def charge_wallet(user, amount):
    return await update_bank(user, -amount, "wallet")

