import logging
import os

from bobcoin.bot import create_bot
from bobcoin.settings import get_token


def main():
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())
    token = get_token()
    if not token:
        raise RuntimeError("Set DISCORD_TOKEN or BOBCOIN_TOKEN before starting the bot.")

    bot = create_bot()
    bot.run(token)


if __name__ == "__main__":
    main()
