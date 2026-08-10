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

4. **AI เงินกู้ (`ai_loan_limit`)** — prompt มีข้อมูล user อาจโดน injection ขอวงเงินสูง
   - มี hard cap 20% ของคลังคุมอยู่แล้ว แต่ควรเพิ่ม: ตรวจ `approved` เป็นตัวเลขล้วน
     (ไม่ใช่แค่ `int()`), log ทุกคำขอ AI + decision, จำกัดต่อ user (เช่น 1 ครั้ง/วัน)
   - ~~**บั๊กที่เทสต์เจอ (2026-08)**~~ ✅ แก้แล้ว — persist `ai_loan_ceiling` ลง doc
     user, `get_loan_info` ใช้ `max(static, AI)` เป็นวงเงิน, และ reset เมื่อชำระหนี้หมด
5. **`$shop` / `$BD`** — `$BD` กันแค่ `has_any_role("Profile")` → เปลี่ยนเป็นเช็ค
   permission จริง (เช่น admin role) หรือจำกัด role ที่ซื้อได้ (whitelist)
6. **Dev command ที่เหลือ** — `$setluck`, `$guardian_run` มี `is_bot_admin` กันอยู่
   แต่ควรย้ายออกไปอยู่หลัง feature flag (เช่น env `GUCOIN_DEV_MODE`)
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

8. **ปรับสมดุลเกม** — โอกาส jackpot สล็อตปัจจุบัน `8/512 ≈ 1.56%`/สปิน อาจสูงไปเมื่อ
   เทียบ multiplier 8x/15x/20x → เก็บสถิติ house win rate แล้วปรับ
9. **ดอกเบี้ยเงินกู้เป็น "กำไรลมๆ"** — `accrue_loan_interest` บวกเข้า `loan_balance`
   โดยไม่ได้มาจากคลัง แล้ว `$house` นับเป็นกำไร — ถูกต้องตามหลักธนาคาร แต่ควรแยก
   แสดง "หนี้ค้างชำระ" กับ "กำไรจริง" ให้ชัด
10. **Firestore cost** — ถ้า user เยอะขึ้น ให้ denormalize leaderboard (เก็บ
    `total` field ไว้ใน doc user ตอนทำ transaction) จะเลิกสแกนทั้ง collection
11. **casino channel auto-delete 45 วิ** — ผลเกมจะหายไปเอง; อาจเพิ่มการ log ไป
    ช่องอื่น (audit) เผื่อ dispute
12. **README/env docs** — `.env.example` + อธิบายตัวแปรทุกตัว
    (`FIREBASE_PROJECT_ID`, `GOOGLE_APPLICATION_CREDENTIALS`, `ANTHROPIC_API_KEY`,
    `GUCOIN_PREFIX`, `GUCOIN_OWNER_ID`, `GUCOIN_ADMIN_ROLE_IDS`)

## 📌 หลักการคงไว้

- เงินทุกเส้นทางต้องผ่าน atomic transaction ใน `bank.py` — ห้าม bypass
- Guardian ต้อง **ลด** luck ได้อย่างเดียว ห้ามเพิ่ม (กัน insider ให้โชค)
- MAX_BET/เดิมพันสูงสุดต้องผูกกับยอดคลังเสมอ
- ฟีเจอร์ใหม่ควรมาพร้อมเทสต์ (`tests/`)
