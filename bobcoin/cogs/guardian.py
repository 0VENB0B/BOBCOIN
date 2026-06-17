import json
import logging

import discord
from discord.ext import commands, tasks

from ..ai import call_ai
from ..bank import (
    get_bank_health,
    get_history,
    get_house_debt,
    get_lucky_users,
    guardian_force_collect,
    guardian_nerf_user,
    guardian_restore_user,
    house_repay_debt,
    _get_db,
    _house_ref,
)
from ..helpers import is_bot_admin

logger = logging.getLogger("bobcoin.guardian")

_GUARDIAN_AI_SYSTEM = (
    "คุณเป็น AI ดูแลสมดุลธนาคารกลาง GUCOIN\n"
    "หน้าที่: วิเคราะห์ข้อมูลผู้เล่นและแนะนำการปรับ luck multiplier แบบ subtle ไม่ให้รู้ตัว\n"
    "luck 1.0=ปกติ, >1.0=โชคดี, ลดลงช้าๆ ไม่เกิน 30% ต่อครั้ง\n"
    "ตอบ JSON เท่านั้น format: {\"actions\": [{\"user_id\": \"123\", \"new_luck\": 2.5, \"reason\": \"...\"}]}\n"
    "ถ้าไม่ควรปรับใคร ตอบ {\"actions\": []}"
)

# guardian_interest_cap values by status
_INTEREST_CAP = {
    "healthy":  0.30,
    "warning":  0.15,
    "critical": 0.05,
    "danger":   0.00,
}


class GuardianCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._guardian_loop.start()

    def cog_unload(self):
        self._guardian_loop.cancel()

    @tasks.loop(minutes=30)
    async def _guardian_loop(self):
        try:
            await self._run()
        except Exception:
            logger.exception("Guardian loop crashed")

    @_guardian_loop.before_loop
    async def _before(self):
        await self.bot.wait_until_ready()

    # ── Main logic ───────────────────────────────────────────────────────────

    async def _run(self):
        health = await get_bank_health()
        status = health["status"]
        bal = health["balance"]
        ratio = health["ratio"]
        logger.info("Guardian: %s  balance=%d  ratio=%.2f%%", status, bal, ratio * 100)

        # Update interest cap based on status
        cap = _INTEREST_CAP[status]
        await _house_ref().set({"guardian_interest_cap": cap, "guardian_status": status}, merge=True)

        lucky = await get_lucky_users()
        boosted = [u for u in lucky if u["luck"] > 1.0]

        if status == "danger":
            await self._nerf_all(boosted, factor=0.50, label="DANGER -50%")
            users, coins = await guardian_force_collect(pct=0.15)
            logger.warning("Guardian DANGER: force-collected %d from %d users", coins, users)

        elif status == "critical":
            await self._nerf_all(boosted, factor=0.75, label="CRITICAL -25%")
            users, coins = await guardian_force_collect(pct=0.08)
            logger.warning("Guardian CRITICAL: force-collected %d from %d users", coins, users)

        elif status == "warning":
            if boosted:
                await self._ai_decide(health, boosted)
            else:
                logger.info("Guardian WARNING: no boosted users to adjust")
            # Still repay debt slowly even in warning (5%)
            house_debt = await get_house_debt()
            if house_debt > 0:
                repaid = await house_repay_debt(int(bal * 0.05))
                if repaid > 0:
                    logger.info("Guardian WARNING: repaid %d house debt", repaid)

        elif status == "healthy":
            # Gradually restore previously nerfed users
            to_restore = [u for u in lucky if u.get("guardian_original_luck") is not None]
            for u in to_restore:
                orig = float(u["guardian_original_luck"])
                cur = u["luck"]
                new_lk = await guardian_restore_user(int(u["id"]), cur, orig)
                if new_lk >= orig:
                    logger.info("Guardian restored user %s luck to %.2f", u["id"], orig)
                else:
                    logger.info("Guardian slowly restoring user %s: %.2f → %.2f (target %.2f)", u["id"], cur, new_lk, orig)

            # Repay house debt — allocate 15% of current balance per cycle
            house_debt = await get_house_debt()
            if house_debt > 0:
                repay_budget = int(bal * 0.15)
                repaid = await house_repay_debt(repay_budget)
                if repaid > 0:
                    logger.info("Guardian repaid %d house debt (remaining ~%d)", repaid, house_debt - repaid)

    async def _nerf_all(self, boosted: list[dict], factor: float, label: str):
        count = 0
        for u in boosted:
            new_lk = max(1.0, u["luck"] * factor)
            if new_lk < u["luck"]:
                await guardian_nerf_user(int(u["id"]), u["luck"], new_lk)
                count += 1
        logger.warning("Guardian %s: nerfed %d users", label, count)

    async def _ai_decide(self, health: dict, boosted: list[dict]):
        """Use AI to decide which lucky users to adjust in warning state."""
        # Fetch recent game history for top 5 by luck
        user_summaries = []
        for u in boosted[:5]:
            history = await get_history(int(u["id"]), limit=15)
            game_history = [e for e in history if e.get("cmd") in ("slot", "flip", "lottery")]
            recent_net = sum(e.get("net", 0) for e in game_history)
            wins = sum(1 for e in game_history if e.get("net", 0) > 0)
            user_summaries.append({
                "id": u["id"],
                "luck": u["luck"],
                "wallet": u["wallet"],
                "deposited": u["deposited"],
                "recent_net_10games": recent_net,
                "wins_in_10games": wins,
                "total_games": len(game_history),
            })

        prompt = (
            f"สถานะธนาคาร: WARNING\n"
            f"ยอดเงิน: {health['balance']:,} (ratio: {health['ratio']:.1%} ของยอดรวมที่เคยเข้า)\n\n"
            f"User ที่มี luck สูง:\n"
            + "\n".join(
                f"- id:{u['id']}  luck:{u['luck']}x  wallet:{u['wallet']:,}  "
                f"กำไร 10 เกมล่าสุด:{u['recent_net_10games']:,}  ชนะ:{u['wins_in_10games']}/{u['total_games']}"
                for u in user_summaries
            )
            + "\n\nวิเคราะห์และแนะนำการปรับ luck แบบ subtle ทำให้เนียน"
        )

        raw = await call_ai(
            _GUARDIAN_AI_SYSTEM,
            [{"role": "user", "content": prompt}],
            fallback="{}",
            max_tokens=400,
        )

        try:
            start, end = raw.find("{"), raw.rfind("}") + 1
            data = json.loads(raw[start:end]) if start >= 0 and end > start else {}
        except (json.JSONDecodeError, ValueError):
            logger.warning("Guardian AI: bad JSON response: %s", raw[:200])
            return

        actions = data.get("actions", [])
        for action in actions:
            uid = str(action.get("user_id", ""))
            new_lk = float(action.get("new_luck", 1.0))
            reason = action.get("reason", "")
            # Safety: only reduce (guardian never boosts), cap reduction at 30% per cycle
            matching = next((u for u in boosted if u["id"] == uid), None)
            if not matching:
                continue
            cur = matching["luck"]
            new_lk = max(cur * 0.70, min(new_lk, cur))  # clamp: at most -30%, never increase
            new_lk = round(max(0.0, new_lk), 3)
            if new_lk < cur:
                await guardian_nerf_user(int(uid), cur, new_lk)
                logger.info("Guardian AI: user %s luck %.2f→%.2f  reason=%s", uid, cur, new_lk, reason)

    # ── DEV command to check guardian status ─────────────────────────────────

    @commands.command(aliases=["guardian", "คลังสุขภาพ"])
    @commands.check(is_bot_admin)
    async def bankhealth(self, ctx):
        health = await get_bank_health()
        lucky = await get_lucky_users()
        boosted = [u for u in lucky if u["luck"] > 1.0]

        _STATUS_COLOR = {
            "healthy":  discord.Color.green(),
            "warning":  discord.Color.yellow(),
            "critical": discord.Color.orange(),
            "danger":   discord.Color.red(),
        }
        _STATUS_ICON = {"healthy": "🟢", "warning": "🟡", "critical": "🟠", "danger": "🔴"}

        house_debt = await get_house_debt()

        em = discord.Embed(title="🛡️ Guardian — Bank Health", color=_STATUS_COLOR[health["status"]])
        em.add_field(name="สถานะ", value=f"{_STATUS_ICON[health['status']]} **{health['status'].upper()}**", inline=True)
        em.add_field(name="ยอดเงิน", value=f"**{health['balance']:,}** 🪙", inline=True)
        em.add_field(name="Health Ratio", value=f"**{health['ratio']:.1%}**", inline=True)
        em.add_field(name="Interest Cap", value=f"**{_INTEREST_CAP[health['status']] * 100:.0f}%**/รอบ", inline=True)
        em.add_field(name="Lucky Users", value=f"**{len(boosted)}** คน (luck > 1.0)", inline=True)
        debt_icon = "🟢" if house_debt == 0 else ("🟡" if house_debt < 10_000_000 else "🔴")
        em.add_field(name="หนี้คลัง", value=f"{debt_icon} **{house_debt:,}** 🪙", inline=True)
        if boosted:
            em.add_field(
                name="Top Luck",
                value="\n".join(f"<@{u['id']}> → **{u['luck']}x**" for u in boosted[:3]),
                inline=False,
            )
        em.set_footer(text="ตรวจสอบทุก 2 ชม. อัตโนมัติ • ชำระหนี้อัตโนมัติเมื่อ healthy")
        await ctx.send(embed=em)

    @commands.command()
    @commands.check(is_bot_admin)
    async def guardian_run(self, ctx):
        """DEV: force run guardian check now."""
        await ctx.send("🛡️ Running guardian check...")
        await self._run()
        health = await get_bank_health()
        await ctx.send(f"✅ Done. Status: **{health['status']}** | Balance: **{health['balance']:,}** 🪙")


async def setup(bot):
    await bot.add_cog(GuardianCog(bot))
