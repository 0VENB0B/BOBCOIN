# BOBCOIN

BOBCOIN is a Discord economy bot (GUCOIN) with games, a central bank, loans,
XP/achievements, an AI-powered bank guardian, and an interactive casino panel.

## Structure

- `main.py`: runtime entrypoint
- `bobcoin/bot.py`: bot factory and cog loading
- `bobcoin/settings.py`: environment and constants
- `bobcoin/bank/`: money layer package — `core` (accounts/atomic transactions),
  `rewards` (interest/XP/achievements/daily/jackpot), `loans`, `debt`, `guardian`
  (bank health/luck/whale-proof bet cap)
- `bobcoin/games.py`: pure game logic (blackjack/RPS/streak math — unit-tested)
- `bobcoin/roblox.py`: Roblox top-map recommendations (live stats via Roblox's public APIs + curated fallback)
- `bobcoin/gameplay.py`: game runners shared by the prefix commands and the casino panel
- `bobcoin/components.py`: Discord Components v2 command menu
- `bobcoin/cogs/`: command groups split by feature area
- `tests/`: pytest suite with an in-memory fake Firestore (no network needed)

## Setup

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python main.py
```

## Fun features

- **`$roblox [แนวเกม|ชื่อแมพ]`** — แนะนำแมพ Roblox ชั้นนำแบบ **ข้อมูลจริงนำ** (rank ตามคนกำลังเล่นจริงจาก API — สุ่มจากท็อป 5 ของแนวที่เลือก) พร้อมสถิติสด (คนกำลังเล่น, ยอดเข้าชม, รูป) ผ่านปุ่ม 🔄 สุ่มแมพใหม่ / 📂 เลือกแนว / 🎮 เปิดเกม / **🎁 โค้ดเกม** (ค้นหาสดจาก Google/Brave ด้วยชื่อแมพที่ได้จาก API) และ **👥 ชวนเพื่อน** เพื่อแท็กเพื่อนให้มาเล่นด้วยกัน (เลือกได้สูงสุด 5 คน) พูดกับบอทว่า "แนะนำแมพ" ก็เปิดได้เหมือนกัน
- **`$rps <amount>`** — เป่ายิ้งฉุบ vs BOB (ปุ่ม 🪨✂️📄) ชนะ 1.8x เสมอได้คืน แถมในคาสิโน panel ด้วย
- **`$bj`** — เพิ่มปุ่ม **Double Down** (เดิมพัน 2 เท่า รับไพ่อีก 1 ใบแล้วจบ) เฉพาะเกมเดี่ยว
- **`$quiz`** — เปลี่ยนเป็นแบบเลือกตอบ 4 ตัวเลือก (ปุ่ม) AI ออกข้อสอบเอง ตัวถูกขึ้นเขียว ตัวผิดขึ้นแดง
- **สล็อต** — กด 🔄 เล่นอีกได้ทันทีด้วยเงินเดิมพันเดิม

For development/testing, also install `requirements-dev.txt` (adds pytest).

### Environment variables (`.env`)

| Variable | Required | Description |
|----------|----------|-------------|
| `DISCORD_TOKEN` | ✅ | Bot token (`GUCOIN_TOKEN` / `BOBCOIN_TOKEN` as fallbacks) |
| `FIREBASE_PROJECT_ID` | ✅ | Firestore project (bank data lives here now) |
| `GOOGLE_APPLICATION_CREDENTIALS` | ✅ | Path to the service-account JSON |
| `ANTHROPIC_API_KEY` | Optional | Enables the BOB chat AI / AI loan approval |
| `GUCOIN_PREFIX` | Optional | Command prefix, defaults to `$` |
| `GUCOIN_OWNER_ID` | Optional | Bot owner Discord ID |
| `GUCOIN_ADMIN_ROLE_IDS` | Optional | Comma-separated admin role IDs (for `$BD`) |
| `GUCOIN_DEV_MODE` | Optional | `1` to enable dev commands (`$setluck`, `$seed`, `$guardian_run`, `$bankhealth`) — still requires an admin identity; `0`/empty keeps them disabled |
| `GUCOIN_BD_ROLE_IDS` | Optional | Comma-separated role IDs `$BD` may sell (empty = sells nothing) |
| `LOG_LEVEL` | Optional | Defaults to `INFO` |
| `GUCOIN_AUDIT_CHANNEL_ID` | Optional | Casino audit channel — copies of deleted game results are forwarded here (`0` disables) |
| `GUCOIN_SLOT_JACKPOT_BASE` | Optional | Slot jackpot rate per spin, default `0.015625` (≈1.56%); tune from `$stats` |
| `GUCOIN_ROBLOX_CODES_URL` | Optional | URL ของ JSON ที่เจ้าของบอทดูแลไว้สำหรับอัปเดตโค้ดเกม Roblox (merge ทับลิสต์ในบอท) — รูปแบบ `{"Blox Fruits": ["CODE1", ...]}` โดยชื่อเกมต้องตรงกับชื่อในบอท (เช่น `Blox Fruits`) |
| `GUCOIN_GOOGLE_API_KEY` | Optional | Google Cloud API key (เปิดใช้งาน Custom Search API) — เปิดการค้นหาโค้ดเกมสดจาก Google |
| `GUCOIN_GOOGLE_CSE_ID` | Optional | Programmable Search Engine ID — ต้องตั้งคู่กับ key ข้างบน (`https://programmablesearchengine.google.com`) |
| `GUCOIN_BRAVE_API_KEY` | Optional | Brave Search API key — ตัวเลือกแทน Google (ฟรี ~2,000 ครั้ง/เดือน, `https://api.search.brave.com`) |
| `GUCOIN_DEV_MODE` | Optional | `1` enables dev/ops commands — `$metrics` (in-process bot health: uptime, commands, AI calls, background-task failures) joins `$setluck`/`$seed`/`$guardian_run`/`$bankhealth` |

> A ready-to-fill template lives in `.env.example`.

> Keep tokens and the service-account JSON out of git (`.gitignore` already covers
> `.env` and `*firebase-adminsdk*.json`). The legacy `mainbank.json` (old JSON-bank
> era) and unused image assets (`T.jpg`, `TEST.jpg`, `TID.jpg`, `Text.png`,
> `picture.jpg`) were removed — the media commands only need `pic.jpg`, `ID.jpg`
> and `white.png`.

## Testing

```bash
source venv/bin/activate
python -m pytest tests/ -v
```

The suite fakes `google.cloud.firestore` entirely, so it runs offline with zero
credentials. **593 tests** — the money layer is at ~100%, every cog including
the interaction-heavy panel, and total `bobcoin/` coverage is **95%**
(fail-under gate in CI). Covering the P2 backlog, the P1
hardening (AI-loan strict parsing + per-day rate limit + audit logging, `$BD`
admin+whitelist, dev commands behind `GUCOIN_DEV_MODE`), the fun round
(RPS, BJ double down, MCQ quiz, slot replay, Roblox rankings + redeem codes):
leaderboard via the denormalized `total` index (`get_leaderboard`, no collection
scan), house win-rate stats (`record_game_outcome` / `$stats`), and the casino
audit-copy (`GUCOIN_AUDIT_CHANNEL_ID`):

- `test_bank.py` / `test_bank_edge.py` — every branch of deposit/withdraw/
  transfer/rob/loans/interest/daily/jackpot/debt incl. invalid inputs, missing
  accounts, budget caps, cache expiry, persisted cooldowns, atomic `rob_transfer`
  and the anti-self-farming transfer-relation check
- `test_guardian.py` — bank health, luck/crisis tiers, whale-proof bet cap,
  nerf/restore, force collection
- `test_invariants.py` — money conservation (wallets + house − debt), no
  negative balances, and atomicity of every rejected operation
- `test_economy.py` — the real command handlers: register flow, deposit/
  withdraw/give money paths, and the rob security fixes end-to-end
  (relation block, persisted cooldown, success + penalty transfers)
- `test_gameplay.py` — the real game runners with a fake ctx: jackpot/wins/
  losses/pushes pay exactly the advertised multiplier, money is conserved, and
  blocked games (bet too big / house closed / no funds) move nothing
- `test_duel.py` — full PvP flow: accept/decline/timeout, flip/slot/BJ winners
  and ties with exact balances + house cut
- `test_panel.py` — casino panel: embed builder, guards, modal validation,
  daily payout, auto-delete (fake interactions)
- `test_fun.py` / `test_media.py` / `test_info.py` — quiz + emoji + geometry
  commands, image pipelines (stonk/DTC/ID card), ping/profile/clear and the
  role/permission gates
- `test_guardian_cog.py` — the guardian loop: danger/critical/warning/healthy
  branches, nerf floors, AI decision clamp (−30%/cycle, never boosts)
- `test_events_cog.py` — `on_message` intent routing, `_handle_game_intent`
  command rewrites, `on_command_error` mapping, `on_ready`; `test_bot.py` —
  factory smoke test + cog wiring + graceful-shutdown `close()` (drains
  background tasks, calls `cog_unload` on every cog)
- `test_intents.py` — the `@BOB` chat→game parser: all-in/half, 5-digit
  lottery tickets, flip sides, amounts with commas
- `test_games.py` / `test_helpers.py` — property/statistical checks on the
  blackjack & streak math, amount parsing, `is_bot_admin`
- `test_movies.py` / `test_ai.py` — TMDb genre/auth/result-cleaning logic,
  `_tmdb_get` error handling, `recommend_movie` routing (search → discover
  fallback when search is empty) and the AI helper's no-key fallback +
  full success/error paths with a faked session (network never hit)
- `test_components.py` — the Components v2 command menu: category buttons
  (success + error callbacks) and view construction/fallback
- P2 extras — `total`-field maintenance on every money write + indexed
  leaderboard ordering, game-stats recording on every settled game (incl.
  blocked games recording nothing), `$stats` embed, audit-copy forwarding when
  configured (and no-op when not), and env parsing for the new settings
- `test_images.py` — asset path resolution, font fallback, PNG file builder,
  avatar download/URL helpers

Measure coverage with:

```bash
python -m coverage run -m pytest tests/ -q && python -m coverage report -m
```

### CI (GitHub Actions)

`.github/workflows/ci.yml` runs on every push/PR to `dev`/`main`: installs deps,
`py_compile` syntax check, an import check of every module (catches lazy-import
bugs), then `pytest` + coverage with a `--fail-under=70` gate on `bobcoin/*`.

## Notes

- `discord.py==2.7.1` is required for the Components v2 command menu.
- Bank data is stored in **Firestore** (collection `users`, `system/bank`,
  `system/jackpot`, `system/debt`). All money moves go through atomic
  transactions in `bank.py`.
- Shutdown is graceful: `GUCoinBot.close()` cancels task loops and drains
  in-flight background tasks (history/XP writes) before closing.
- See `ROADMAP.md` for the prioritized backlog (cooldown persistence, rob
  atomicity, AI-loan hardening, etc.).
