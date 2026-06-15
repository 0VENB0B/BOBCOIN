# BOBCOIN

BOBCOIN is a Discord economy bot.

## Structure

- `main.py`: runtime entrypoint
- `bobcoin/bot.py`: bot factory and cog loading
- `bobcoin/settings.py`: environment and constants
- `bobcoin/bank.py`: atomic JSON bank storage
- `bobcoin/components.py`: Discord Components v2 command menu
- `bobcoin/cogs/`: command groups split by feature area

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
- `discord.py==2.7.1` is required for the Components v2 command menu.
