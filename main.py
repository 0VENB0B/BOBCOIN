import logging
import os

from dotenv import load_dotenv

from bobcoin.bot import create_bot
from bobcoin.settings import get_token


def main():
    load_dotenv()
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logging.getLogger("bobcoin").info("BOBCOIN starting…")
    token = get_token()
    if not token:
        raise RuntimeError("Set DISCORD_TOKEN or BOBCOIN_TOKEN before starting the bot.")

    bot = create_bot()
    bot.run(token)


if __name__ == "__main__":
    main()
