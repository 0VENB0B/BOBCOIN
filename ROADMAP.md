# BOBCOIN — Roadmap

จัดลำดับงานตามความเสี่ยง (priority สูง = อันตราย/เสียเงิน/เสียความเชื่อมั่น)

สถานะปัจจุบัน: ทุก transaction เงินใช้ Firestore atomic transaction, ระบบ Guardian
ดูแลสมดุลคลัง, เทสต์อัตโนมัติเริ่มมีแล้ว (ดู README → Testing)

---

## ✅ ทำแล้ว (รอบนี้)

- **กันปลาวาฬล้างคลัง** — เดิมพันสูงสุดถูกจำกัดตามยอดคลังหลวง (2% ต่อเดิมพัน,
  floor 1,000) ใช้กับทุกเกม + duel (`max_bet_allowed` ใน `bank.py`)
- **ลบ `$TestJson`** — dev command แจกเงินฟรี หลงเหลือจากยุคเก่า
- **แก้ `$leaderboard` crash** — `fetch_user` ของ user ที่ถูกลบจะไม่พังทั้งคำสั่ง
- **TTL cache 60 วิ** — `get_bank_data()` (leaderboard) และ
  `get_total_outstanding_loans()` ($house / panel) เลิกสแกนทั้ง collection ทุกครั้ง
- **แยก game logic** → `bobcoin/games.py` (pure, เทสต์ได้) และรวมโค้ดซ้ำ
  house-status เป็น `house_status_band()`
- **ระบบเทสต์** — 460 เทสต์ (bank/games/helpers/components/movies/images/ai
  100%, ทั้ง `bobcoin/` 91% พร้อม CI fail-under gate): ครบทุก edge case (input
  ผิด, บัญชีหาย, budget cap, cache expiry, กันปลาวาฬ, guardian/luck/
  force-collect) + invariant tests (เงินอนุรักษ์, ไม่ติดลบ, failed op ไม่แตะ
  state) + property/statistical tests ของเกม + command-level tests ของทุก cog
  รวม panel views/modals/setup + economy loan/repay/history/admin commands
- **CI pipeline (GitHub Actions)** — `.github/workflows/ci.yml`: รันทุก push/PR
  ไป `dev`/`main` = ติดตั้ง deps + **`ruff check` (lint, config ใน
  `pyproject.toml`)** + `py_compile` + import check ทุกโมดูล (จับ lazy-import
  ผิดทาง) + `pytest` + coverage `--fail-under=70`
- **Lint (ruff)** — `pyproject.toml` กำหนด rule set: E4/E7/E9/F (correctness),
  I (import sorting), UP/SIM/B/RUF — ผ่านสะอาดทั้ง `bobcoin/` และ `tests/`
- **เก็บ reference ของ background task** — helper `_spawn()` ใน `gameplay.py`
  (ใช้ร่วมทุก cog) hold task ไว้จนเสร็จ ป้องกัน CPython GC ยกเลิก task กลางคัน
  (RUF006) และลบ legacy files (`mainbank.json`, ภาพ asset เก่า) ทิ้งแล้ว
- **แก้ `recommend_movie`** — ถ้า search ได้ผลว่าง ตอนนี้ fallback ไป discover
  แทนที่จะคืน None (ก่อนหน้านี้ user ไม่ได้คำแนะนำเลย)
- **เจอบั๊กใน fake Firestore** — `FakeTransaction.set` ไม่รองรับ `merge=True`
  (ในของจริง merge เก็บ field อื่นไว้) — เทสต์แบบละเอียดจับได้และแก้แล้ว
- **Cooldown ย้ายเข้า Firestore แล้ว** — `get_cooldown`/`set_cooldown` ใน bank
  core (เก็บใน doc user ใต้ field `cd`) — รีสตาร์ทบอทแล้ว cooldown ไม่หาย
  (`$rob` 2 ชม. + ทุกเกมใน panel)
- **`$rob` เป็น atomic แล้ว** — `rob_transfer()` ใน bank core ย้ายเงินเป้า→ผู้ปล้น
  (และค่าปรับกลับทาง) ใน transaction เดียว + log history `rob`/`robbed`

## 🔴 P0 — ทำก่อน (ช่องโหว่เชิงปฏิบัติ)

1. ~~**Cooldown เก็บใน RAM**~~ ✅ ทำแล้ว — เก็บใน Firestore (`cd` ใน doc user)
   ทั้ง `$rob` (2 ชม./คู่) และเกม panel (30/15/20/120 วิ)
2. ~~**`$rob` ไม่ atomic**~~ ✅ ทำแล้ว — `rob_transfer()` atomic ใน bank core
   (หักเป้า + โอนผู้ปล้น transaction เดียว; ค่าปรับตอนโดนจับก็ atomic เช่นกัน)
3. ~~**`$rob` ปล้นตัวเองข้ามบัญชี (self-farming)**~~ ✅ ทำแล้ว — `has_transfer_relation()`
   ใน bank core ตรวจ history ว่าเคย `$give` ระหว่างสองบัญชีไหม (ทั้งสองทิศทาง)
   ถ้าเคย → block `$rob` ทันที

## 🟠 P1 — ควรทำ

4. ~~**AI เงินกู้ (`ai_loan_limit`)**~~ ✅ ทำแล้ว — เพิ่ม `_extract_approved()` ตรวจ
   `approved` เป็น int ล้วน (reject float/exponent/bool/string ยาว — `"1e10"`,
   `1500.5`, `true` เคยหลุดผ่าน `int()` ได้), **log ทุกคำขอ + decision** ลง history
   (`ai_loan` entry: requested/approved/raw), และ **rate limit 1 ครั้ง/วัน/คน**
   (cooldown `ai_loan` ใน Firestore) — กัน injection ขอวงเงินซ้ำๆ
5. ~~**`$shop` / `$BD`**~~ ✅ ทำแล้ว — `$BD` เปลี่ยนจาก `has_any_role("Profile")`
   เป็น **admin-only** (`is_bot_admin`) + **whitelist role ที่ขายได้** ผ่าน env
   `GUCOIN_BD_ROLE_IDS` (ว่าง = ขายไม่ได้เลย; เช็คใน `_buy_role` ก่อนหักเงิน)
6. ~~**Dev command ที่เหลือ**~~ ✅ ทำแล้ว — `$setluck`, `$seed`, `$guardian_run`,
   `$bankhealth` ย้ายไปอยู่หลัง **feature flag `GUCOIN_DEV_MODE`** (ต้อง flag ON
   *และ* เป็น admin — แม้เป็น admin ถ้า flag ปิดก็ใช้ไม่ได้) ผ่าน `is_dev_mode()`
   ใน helpers
7. ~~**ขยายเทสต์**~~ ✅ ทำแล้ว — `_parse_intent` ครบ, game runners
   (`gameplay.py` 78%), duel 73%, **panel 38→91%** (embed/guard/modals ทุกตัว/
   ปุ่ม view ครบ/setup/on_message/auto-delete), **events 41→99%** (on_message/
   intent/error handler), **fun 0→95%** (quiz/emoji/เรขาคณิต/mrp), **media
   0→98%**, **info 0→97%** (ping/profile/clear + role/permission gates),
   **guardian cog 0→74%** (ทุก status + AI clamp), **economy 15→92%** (ทุก
   คำสั่งเงิน รวม loan/repay/history/admin + `_buy_role`), **bot factory + cog
   wiring**, **movies 27→100%** (รวม `_tmdb_get`/routing), **ai 0→100%**
   (no-key + success/error paths), **components 0→100%**, **images 0→100%**

## 🟡 P2 — ค่อยๆ ทำ

8. ~~**ปรับสมดุลเกม**~~ ✅ ทำแล้ว (โครงสร้าง) — เพิ่ม `record_game_outcome`/
   `get_game_stats` เก็บสถิติ house win rate ต่อเกม (slot/flip/lottery/bj) ลง
   Firestore ทุกครั้งที่เกมจบ + `$stats` command ดูผล และ jackpot base rate ย้ายไป
   อยู่ที่ `settings.SLOT_JACKPOT_BASE` (env `GUCOIN_SLOT_JACKPOT_BASE`) — ปรับ
   สมดุลได้โดยไม่ต้องแก้โค้ด รอสถิติจริงสะสมแล้วค่อยปรับค่าจากข้อมูล
9. ~~**ดอกเบี้ยเงินกู้เป็น "กำไรลมๆ"**~~ ✅ ทำแล้ว — `$house`/panel แยก field
   "💸 ยอดหนี้ค้างชำระ" กับ "📊 กำไรสุทธิ" ชัดเจน (สูตรไม่นับเงินต้นกู้)
10. ~~**Firestore cost**~~ ✅ ทำแล้ว — denormalize: ทุก transaction ที่แตะ
    wallet/deposited (core/loans/rewards/guardian) เขียน `total` field กำกับไว้ใน
    doc user ด้วย → `get_leaderboard()` ใช้ query `order_by("total", DESC)` ตัวเดียว
    เลิกสแกนทั้ง collection (`$leaderboard` + ปุ่ม panel lb ใช้แล้ว)
11. ~~**casino channel auto-delete 45 วิ**~~ ✅ ทำแล้ว — `_audit_copy()` ส่งสำเนา
    embed/ข้อความของผลเกมไปช่องที่ตั้ง `GUCOIN_AUDIT_CHANNEL_ID` ก่อนถูกลบ (เผื่อ
    dispute) — ปิดได้ด้วย 0
12. ~~**README/env docs**~~ ✅ ทำแล้ว — สร้าง `.env.example` ครอบคลุมทุกตัวแปร
    (`DISCORD_TOKEN`, `FIREBASE_PROJECT_ID`, `GOOGLE_APPLICATION_CREDENTIALS`,
    `ANTHROPIC_API_KEY`, `GUCOIN_PREFIX`, `GUCOIN_OWNER_ID`,
    `GUCOIN_ADMIN_ROLE_IDS`, `LOG_LEVEL`, + ใหม่ `GUCOIN_AUDIT_CHANNEL_ID`,
    `GUCOIN_SLOT_JACKPOT_BASE`) และ README มีตารางอธิบายครบ

## 🔵 P3 — Ops / dev (graceful shutdown, metrics, logging)

13. ~~**Graceful shutdown ของ background tasks**~~ ✅ ทำแล้ว — `drain_background_tasks()`
    ใน `gameplay.py` รอ fire-and-forget tasks (history/XP/achievement) จบภายใน
    timeout ก่อนปิด (เหลือค้าง → cancel + log) และ `GUCoinBot.close()`
    override ให้เรียก `cog_unload` ของทุก cog (หยุด task loops) ก่อน drain แล้วค่อย
    ปิด connection — ป้องกันข้อมูลหายกลางคันตอน restart/deploy
14. ~~**Metrics ใน process**~~ ✅ ทำแล้ว — `bobcoin/metrics.py` counters/gauges/
    uptime เบาๆ (ไม่มี dependency) + `$metrics` (dev mode) ดู: commands ต่อชื่อ,
    AI calls/success/failures, background task failures, event errors, guild
    count, uptime — ใช้เฝ้าสุขภาพบอทโดยไม่ต้องพึ่ง external service
15. ~~**Logging ดีขึ้น**~~ ✅ ทำแล้ว — `main.py` format มาตรฐาน (timestamp +
    level + logger name), log เวลาโหลด cogs ใน `setup_hook`, log ready พร้อม
    จำนวน guild, `on_error` listener จับ event exception ที่เคยเงียบหาย

## 📌 หลักการคงไว้

- เงินทุกเส้นทางต้องผ่าน atomic transaction ใน `bank.py` — ห้าม bypass
- Guardian ต้อง **ลด** luck ได้อย่างเดียว ห้ามเพิ่ม (กัน insider ให้โชค)
- MAX_BET/เดิมพันสูงสุดต้องผูกกับยอดคลังเสมอ
- ฟีเจอร์ใหม่ควรมาพร้อมเทสต์ (`tests/`)
