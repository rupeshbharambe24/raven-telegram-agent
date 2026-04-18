#!/usr/bin/env python3
"""AI Agent - Telegram-controlled local LLM agent with permission system."""

import logging
from config import Config
from core.bot import create_bot

# Ensure log directory exists
log_dir = (Config.WORKSPACE / Config.LOG_FILE).parent
log_dir.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(str(Config.WORKSPACE / Config.LOG_FILE)),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)


def main():
    logger.info("Starting AI Agent...")
    logger.info(f"Ollama URL: {Config.OLLAMA_URL}")
    logger.info(f"Workspace: {Config.WORKSPACE}")
    logger.info(f"Models: {Config.DEFAULT_MODEL}, {Config.CODE_MODEL}")

    bot = create_bot()
    logger.info("Bot created. Starting Telegram polling...")
    bot.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
