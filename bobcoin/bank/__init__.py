"""Money layer for BOBCOIN, split by concern:

- ``core``      — Firestore access, atomic transactions, accounts, wallet/house moves
- ``rewards``   — interest, XP, achievements, daily claim, jackpot
- ``loans``     — credit limits, take/repay, interest accrual, AI approval
- ``debt``      — house debt ceiling, auto-borrow, repayment
- ``guardian``  — bank health, luck controls, whale-proof bet cap, force collection

Every public name is re-exported here so existing ``from ..bank import X``
statements keep working unchanged.
"""

from .core import (
    _Abort,
    _BANK_FLOOR,
    _cache,
    _cache_get,
    _cache_set,
    _db,
    _get_db,
    _house_ref,
    _in_txn,
    _parse,
    _positive_amount,
    _ref,
    charge_wallet,
    get_bank_data,
    get_balance,
    get_cooldown,
    get_house_balance,
    get_house_data,
    get_history,
    has_transfer_relation,
    is_registered,
    log_history,
    open_account,
    house_payout,
    house_receive,
    rob_transfer,
    set_cooldown,
    transfer_to_user,
    update_bank,
    user_deposit,
    user_withdraw,
)
from .debt import (
    _debt_ref,
    get_house_debt,
    house_auto_borrow,
    house_repay_debt,
)
from .guardian import (
    get_bank_health,
    get_effective_luck,
    get_lucky_users,
    get_user_luck,
    guardian_force_collect,
    guardian_nerf_user,
    guardian_restore_user,
    house_can_pay_games,
    house_status_band,
    max_bet_allowed,
    set_user_luck,
)
from .loans import (
    accrue_loan_interest,
    ai_loan_limit,
    calc_loan_limit,
    get_loan_info,
    get_total_outstanding_loans,
    repay_loan,
    take_loan,
)
from .rewards import (
    ACHIEVEMENTS,
    add_xp,
    calc_interest,
    contribute_jackpot,
    get_achievements,
    get_jackpot_pool,
    grant_achievement,
    pay_interest_all,
    trigger_jackpot,
    try_daily,
    xp_to_level,
)
