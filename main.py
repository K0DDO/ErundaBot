"""Точка входа Discord-бота «Ерунда»."""

from __future__ import annotations

import logging
import os
import sys

import discord
from dotenv import load_dotenv

from bot.bot import create_bot_from_env


def main() -> None:
    load_dotenv()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if not discord.opus.is_loaded():
        try:
            discord.opus._load_default()
        except Exception:
            logging.exception("Failed to load libopus — voice listen will not work")
        else:
            if discord.opus.is_loaded():
                logging.info("libopus loaded")
            else:
                logging.error("libopus still not loaded — install libopus0 in the image")

    token = os.getenv("DISCORD_TOKEN")
    if not token:
        logging.error("DISCORD_TOKEN is missing. Copy .env.example to .env and set the token.")
        sys.exit(1)

    bot = create_bot_from_env()
    bot.run(token, log_handler=None)


if __name__ == "__main__":
    main()
