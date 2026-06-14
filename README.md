# BOBCOIN

BOBCOIN is a Discord economy bot.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export DISCORD_TOKEN="your-bot-token"
python main.py
```

Optional environment variables:

- `BOBCOIN_PREFIX`: command prefix, defaults to `$`
- `BOBCOIN_TOKEN`: fallback token name if `DISCORD_TOKEN` is not set
- `LOG_LEVEL`: logging level, defaults to `INFO`

## Notes

- Keep tokens in environment variables or `.env` files. Never commit real tokens.
- Bank data is stored in `mainbank.json` and written atomically to reduce corruption risk.
