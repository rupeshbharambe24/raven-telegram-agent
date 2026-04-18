import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from tools.process_ops import run_script

logger = logging.getLogger(__name__)


class ProcessMonitor:
    """Run scripts, capture errors, offer LLM-based fixing."""

    def __init__(self):
        self._last_error: dict[int, dict] = {}  # chat_id -> error info

    async def run_and_report(self, bot, chat_id: int, script_path: str):
        """Run a script and report results via Telegram."""
        await bot.send_message(chat_id, f"Running {script_path} ...")

        returncode, stdout, stderr = await run_script(script_path)

        if returncode == 0:
            output = stdout.strip() or "(no output)"
            if len(output) > 3000:
                output = output[:3000] + "\n... (truncated)"
            await bot.send_message(chat_id, f"Script completed successfully\n\n{output}")
            return True, output

        # Error occurred
        error_msg = stderr.strip() or stdout.strip() or "Unknown error"
        if len(error_msg) > 2000:
            error_msg = error_msg[:2000] + "\n... (truncated)"

        self._last_error[chat_id] = {
            "script": script_path,
            "error": stderr.strip(),
            "stdout": stdout.strip(),
        }

        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("Ask LLM to fix", callback_data=f"fix:llm:{chat_id}"),
            InlineKeyboardButton("I'll handle it", callback_data=f"fix:manual:{chat_id}"),
        ]])

        await bot.send_message(
            chat_id,
            f"Script failed (exit code {returncode})\n\n{error_msg}",
            reply_markup=keyboard,
        )
        return False, error_msg

    def get_last_error(self, chat_id: int) -> dict | None:
        return self._last_error.get(chat_id)

    def clear_error(self, chat_id: int):
        self._last_error.pop(chat_id, None)
