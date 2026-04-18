import asyncio
import functools
import logging
import re
from pathlib import Path

from telegram import Update, BotCommand, InputFile
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)

from config import Config
from core.guard import Guard
from core.llm_cascade import SmartLLM
from core.notebook import NotebookManager
from core import brain
from core.monitor import ProcessMonitor
from tools import file_ops, process_ops, screenshot, system_info, git_ops

logger = logging.getLogger(__name__)

guard = Guard()
llm = SmartLLM()
monitor = ProcessMonitor()
notebook_mgr = NotebookManager()

MAX_MSG = 4000  # Telegram limit is 4096, leave headroom

# ── JARVIS personality ────────────────────────────────────────────

JARVIS_SYSTEM = (
    "You are JARVIS, a highly capable AI assistant for Rupesh. "
    "You are professional, concise, and slightly witty — like Tony Stark's JARVIS. "
    "Address the user as 'sir' occasionally but not every message. "
    "Keep responses brief and actionable. When reporting status, use structured formatting. "
    "You have full control of the user's development machine via WSL2 and can run commands, "
    "manage files, execute notebooks, and monitor processes."
)

# ── Reminders ─────────────────────────────────────────────────────

_reminders: list[dict] = []  # {task: asyncio.Task, time: float, message: str}


# ── Auth decorator ────────────────────────────────────────────────

def authorized(func):
    """Only allow messages from the configured chat ID."""
    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat = update.effective_chat
        if not chat or chat.id != Config.TELEGRAM_CHAT_ID:
            if chat:
                logger.warning(f"Unauthorized access from chat_id={chat.id}")
            return
        return await func(update, context)
    return wrapper


# ── Helpers ───────────────────────────────────────────────────────

async def typing(update):
    """Show typing indicator."""
    try:
        await update.effective_chat.send_action("typing")
    except Exception:
        pass


async def reply(message, text):
    """Send a text reply, splitting into chunks if too long."""
    if len(text) <= MAX_MSG:
        await message.reply_text(text)
    else:
        for i in range(0, len(text), MAX_MSG):
            await message.reply_text(text[i : i + MAX_MSG])


async def _handle_send_file(update: Update, search_info: str):
    """Search for and send a file based on natural language description."""
    parts = search_info.split("::", 1)
    directory = parts[0] if parts else ""
    query = parts[1] if len(parts) > 1 else ""

    if not query or len(query.strip()) < 2:
        await update.message.reply_text(
            "I couldn't figure out which file you want.\n"
            "Try: /send <exact-path>\n"
            "Or describe it more: 'send me the report.pptx from /mnt/d/projects'"
        )
        return

    await update.message.reply_text(f"Searching for '{query.strip()}' in {directory} ...")

    matches = file_ops.search_files(directory, query)

    if not matches:
        await update.message.reply_text(
            f"No files matching '{query.strip()}' found in {directory}\n\n"
            f"Tip: use /ls {directory} to see what's there."
        )
        return

    if len(matches) == 1:
        # Single match — send it directly
        path = str(matches[0])
        fpath, error = file_ops.get_file_for_send(path)
        if error:
            await update.message.reply_text(f"Found {matches[0].name} but can't send: {error}")
            return
        with open(fpath, "rb") as f:
            await update.message.reply_document(document=f, filename=matches[0].name)
    else:
        # Multiple matches — list them and ask user to pick
        lines = [f"Found {len(matches)} files:\n"]
        for i, m in enumerate(matches, 1):
            sz = m.stat().st_size
            if sz < 1024:
                size_str = f"{sz}B"
            elif sz < 1024 * 1024:
                size_str = f"{sz // 1024}KB"
            else:
                size_str = f"{sz // (1024 * 1024)}MB"
            lines.append(f"  {i}. {m.name}  ({size_str})\n     {m.parent}")
        lines.append(f"\nSend the exact file with:\n/send <path>")
        await reply(update.message, "\n".join(lines))


def _resolve_repo_path(context) -> str:
    """Figure out the git repo path from context or fall back to WORKSPACE."""
    last_file = context.chat_data.get("last_file_path")
    if last_file:
        p = Path(last_file)
        # Walk up to find .git
        check = p if p.is_dir() else p.parent
        while check != check.parent:
            if (check / ".git").exists():
                return str(check)
            check = check.parent
    return str(Config.WORKSPACE)


def _wsl_to_win(wsl_path: str) -> str:
    """Convert /mnt/c/... to C:\\..."""
    if wsl_path.startswith("/mnt/"):
        parts = wsl_path[5:].split("/", 1)
        drive = parts[0].upper()
        rest = parts[1] if len(parts) > 1 else ""
        return f"{drive}:\\{rest}".replace("/", "\\")
    return wsl_path


# ── /start and /help ─────────────────────────────────────────────

@authorized
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await typing(update)
    await update.message.reply_text(
        "[ J.A.R.V.I.S. ONLINE ]\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Good to see you, sir.\n"
        "All systems operational.\n\n"
        "Send /help for the command menu,\n"
        "or just tell me what you need.\n\n"
        "Read ops  =  auto-allowed\n"
        "Write ops =  requires your approval"
    )


@authorized
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await reply(update.message,
        "COMMANDS\n\n"
        "LLM:\n"
        "  /ask <prompt>  - Ask LLM (cascade)\n"
        "  /code <prompt> - Ask code model\n\n"
        "Files:\n"
        "  /read <path>   - Read file [auto]\n"
        "  /ls <path>     - List directory [auto]\n"
        "  /send <path>   - Send file to you [auto]\n"
        "  /write <path>  - Write file [approval]\n"
        "  /delete <path> - Delete file [approval]\n"
        "  /find <name>   - Search for files [auto]\n"
        "  /tree <path>   - Directory tree [auto]\n"
        "  /recent <path> - Recently modified files [auto]\n\n"
        "Process:\n"
        "  /run <script.py> - Run Python script [approval]\n"
        "  /watch <script>  - Run with live output [approval]\n"
        "  /cmd <command>   - Run shell command [approval]\n"
        "  /tail <logfile>  - Monitor log file [auto]\n"
        "  /procs           - Show background processes [auto]\n"
        "  /kill <name>     - Kill background process [approval]\n"
        "  /history         - Command history [auto]\n\n"
        "Notebook:\n"
        "  /nb open <path>  - Load notebook\n"
        "  /nb run          - Run all cells [approval]\n"
        "  /nb run 5        - Run cell 5 [approval]\n"
        "  /nb run 5+       - Run from cell 5 onward [approval]\n"
        "  /nb cell 5       - Show cell source\n"
        "  /nb edit 5       - Edit cell (next msg = new content)\n"
        "  /nb out 5        - Show cell output\n"
        "  /nb status       - Kernel status\n"
        "  /nb stop         - Shutdown kernel\n"
        "  /nb env <path>   - Set Python env\n\n"
        "Git:\n"
        "  /commit <msg>    - Git commit [approval]\n"
        "  /diff            - Show git diff [auto]\n"
        "  /gitlog <n>      - Recent commits [auto]\n"
        "  /undo            - Undo last commit [approval]\n\n"
        "System:\n"
        "  /do <task>       - Complex multi-step task [approval]\n"
        "  /screenshot      - Capture screen [auto]\n"
        "  /status          - System status [auto]\n"
        "  /models          - List Ollama models [auto]\n"
        "  /logs            - Recent agent logs [auto]\n"
        "  /apply           - Apply LLM-suggested fix [approval]\n\n"
        "Utility:\n"
        "  /open <path>     - Open in Explorer [auto]\n"
        "  /clip <text>     - Copy to clipboard [auto]\n"
        "  /bookmark <name> <path> - Save bookmark\n"
        "  /go <name>       - List bookmark dir\n\n"
        "Or just type freely to chat with the LLM."
    )


# ── LLM commands ──────────────────────────────────────────────────

@authorized
async def cmd_ask(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prompt = " ".join(context.args) if context.args else ""
    if not prompt:
        await update.message.reply_text("Usage: /ask <your question>")
        return
    await typing(update)
    thinking = await update.message.reply_text("Processing, sir...")
    response = await llm.ask(prompt, system_prompt=JARVIS_SYSTEM)
    try:
        await thinking.edit_text(response or "No response.")
    except Exception:
        await reply(update.message, response or "No response.")


@authorized
async def cmd_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prompt = " ".join(context.args) if context.args else ""
    if not prompt:
        await update.message.reply_text("Usage: /code <your question>")
        return
    await typing(update)
    thinking = await update.message.reply_text("Analyzing code, sir...")
    response = await llm.ask_code(prompt)
    try:
        await thinking.edit_text(response or "No response.")
    except Exception:
        await reply(update.message, response or "No response.")


# ── File commands ─────────────────────────────────────────────────

@authorized
async def cmd_read(update: Update, context: ContextTypes.DEFAULT_TYPE):
    filepath = " ".join(context.args) if context.args else ""
    if not filepath:
        await update.message.reply_text("Usage: /read <filepath>")
        return
    context.chat_data["last_file_path"] = filepath
    content = file_ops.read_file(filepath)
    await reply(update.message, f"{filepath}:\n\n{content}")


@authorized
async def cmd_ls(update: Update, context: ContextTypes.DEFAULT_TYPE):
    dirpath = " ".join(context.args) if context.args else str(Config.WORKSPACE)
    content = file_ops.list_dir(dirpath)
    await reply(update.message, f"{dirpath}:\n\n{content}")


@authorized
async def cmd_write(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    # Parse: /write <path>\n<content>
    remainder = text[len("/write"):].strip()
    if not remainder:
        await update.message.reply_text("Usage:\n/write <path>\n<content on next lines>")
        return

    lines = remainder.split("\n", 1)
    filepath = lines[0].strip()
    content = lines[1] if len(lines) > 1 else ""

    if not content:
        await update.message.reply_text("No content provided.\nUsage:\n/write <path>\n<content on next lines>")
        return

    approved = await guard.request_permission(
        context.bot, update.effective_chat.id,
        "write_file",
        f"Write to {filepath}",
        f"{len(content)} chars:\n{content[:200]}",
    )
    if approved:
        result = file_ops.write_file(filepath, content)
        await update.message.reply_text(result)
        context.chat_data["last_file_path"] = filepath
        # Offer git commit if inside a repo
        await _offer_git_commit(update, context, filepath, f"update {Path(filepath).name}")


@authorized
async def cmd_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    filepath = " ".join(context.args) if context.args else ""
    if not filepath:
        await update.message.reply_text("Usage: /delete <filepath>")
        return

    approved = await guard.request_permission(
        context.bot, update.effective_chat.id,
        "delete_file",
        f"Delete {filepath}",
    )
    if approved:
        result = file_ops.delete_file(filepath)
        await update.message.reply_text(result)


@authorized
async def cmd_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    filepath = " ".join(context.args) if context.args else ""
    if not filepath:
        await update.message.reply_text("Usage: /send <filepath>")
        return
    path, error = file_ops.get_file_for_send(filepath)
    if error:
        await update.message.reply_text(f"Error: {error}")
        return
    with open(path, "rb") as f:
        await update.message.reply_document(document=f, filename=Path(path).name)


@authorized
async def cmd_find(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Search for files across all allowed paths. Usage: /find <name>"""
    query = " ".join(context.args) if context.args else ""
    if not query:
        await update.message.reply_text("Usage: /find <filename or keyword>")
        return

    searching = await update.message.reply_text(f"Searching for '{query}'...")
    matches = file_ops.find_files(query)

    if not matches:
        try:
            await searching.edit_text(f"No files found matching '{query}'.")
        except Exception:
            await update.message.reply_text(f"No files found matching '{query}'.")
        return

    lines = [f"Found {len(matches)} file(s) matching '{query}':\n"]
    for i, m in enumerate(matches, 1):
        try:
            sz = m.stat().st_size
            if sz < 1024:
                size_str = f"{sz}B"
            elif sz < 1024 * 1024:
                size_str = f"{sz // 1024}KB"
            else:
                size_str = f"{sz // (1024 * 1024)}MB"
        except OSError:
            size_str = "?"
        lines.append(f"  {i}. {m.name}  ({size_str})\n     {m.parent}")

    result = "\n".join(lines)
    try:
        await searching.edit_text(result)
    except Exception:
        await reply(update.message, result)


@authorized
async def cmd_tree(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show directory tree. Usage: /tree <path> or /tree (workspace)"""
    dirpath = " ".join(context.args) if context.args else str(Config.WORKSPACE)
    result = file_ops.tree(dirpath)
    await reply(update.message, result)


@authorized
async def cmd_recent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show recently modified files. Usage: /recent or /recent <path>"""
    dirpath = " ".join(context.args) if context.args else None
    result = file_ops.recent_files(dirpath)
    await reply(update.message, result)


# ── Process commands ─────────────────────────────────────────────

@authorized
async def cmd_run(update: Update, context: ContextTypes.DEFAULT_TYPE):
    filepath = " ".join(context.args) if context.args else ""
    if not filepath:
        await update.message.reply_text("Usage: /run <script.py>")
        return

    approved = await guard.request_permission(
        context.bot, update.effective_chat.id,
        "run_script",
        f"Run script: {filepath}",
    )
    if approved:
        await monitor.run_and_report(context.bot, update.effective_chat.id, filepath)


@authorized
async def cmd_watch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Run script with live output streaming. Usage: /watch <script.py>"""
    filepath = " ".join(context.args) if context.args else ""
    if not filepath:
        await update.message.reply_text("Usage: /watch <script.py>")
        return

    approved = await guard.request_permission(
        context.bot, update.effective_chat.id,
        "run_script",
        f"Watch script: {filepath}",
    )
    if not approved:
        return

    status_msg = await update.message.reply_text(f"Running {filepath} (live output)...")
    chunk_count = 0

    async def output_callback(chunk: str):
        nonlocal chunk_count, status_msg
        chunk_count += 1
        text = chunk.strip()
        if not text:
            return
        # Truncate chunk for Telegram
        if len(text) > MAX_MSG - 100:
            text = text[:MAX_MSG - 100] + "\n... (truncated)"
        try:
            if chunk_count <= 1:
                await status_msg.edit_text(f"OUTPUT:\n{text}")
            else:
                await context.bot.send_message(
                    update.effective_chat.id,
                    f"OUTPUT (chunk {chunk_count}):\n{text}",
                )
        except Exception as e:
            logger.warning(f"Watch callback error: {e}")

    returncode, full_output = await process_ops.watch_script(
        filepath, callback=output_callback,
    )

    status = "completed" if returncode == 0 else f"failed (exit {returncode})"
    await context.bot.send_message(
        update.effective_chat.id,
        f"Script {status}.",
    )


@authorized
async def cmd_tail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Monitor a log file. Usage: /tail <logfile>"""
    filepath = " ".join(context.args) if context.args else ""
    if not filepath:
        await update.message.reply_text("Usage: /tail <logfile>")
        return

    target = Path(filepath)
    if not target.exists():
        await update.message.reply_text(f"File not found: {filepath}")
        return

    await update.message.reply_text(f"Tailing {filepath} (send /kill to stop)...")

    async def tail_callback(new_lines: str):
        text = new_lines.strip()
        if not text:
            return
        if len(text) > MAX_MSG - 50:
            text = text[:MAX_MSG - 50] + "\n..."
        try:
            await context.bot.send_message(
                update.effective_chat.id,
                f"TAIL {Path(filepath).name}:\n{text}",
            )
        except Exception as e:
            logger.warning(f"Tail callback error: {e}")

    # Run tail as a background asyncio task
    tail_task = asyncio.create_task(
        process_ops.tail_file(filepath, callback=tail_callback)
    )

    # Create a fake process-like object for tracking
    class _TailHandle:
        def __init__(self, task):
            self._task = task
            self.pid = id(task)
            self.returncode = None
        def terminate(self):
            self._task.cancel()
            self.returncode = -1

    handle = _TailHandle(tail_task)
    process_ops.track_process(
        f"tail:{Path(filepath).name}", handle, f"tail {filepath}", str(target.parent)
    )


@authorized
async def cmd_procs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show running background processes."""
    result = process_ops.get_running_procs()
    await update.message.reply_text(result)


@authorized
async def cmd_kill(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Kill a background process. Usage: /kill or /kill <name> or /kill all"""
    name = " ".join(context.args) if context.args else None

    if name and name.lower() == "all":
        approved = await guard.request_permission(
            context.bot, update.effective_chat.id,
            "run_script",
            "Kill ALL background processes",
        )
        if approved:
            result = await process_ops.kill_process("all")
            await update.message.reply_text(result)
        return

    if name:
        approved = await guard.request_permission(
            context.bot, update.effective_chat.id,
            "run_script",
            f"Kill process: {name}",
        )
        if approved:
            result = await process_ops.kill_process(name)
            await update.message.reply_text(result)
    else:
        # Kill most recent
        approved = await guard.request_permission(
            context.bot, update.effective_chat.id,
            "run_script",
            "Kill most recent background process",
        )
        if approved:
            result = await process_ops.kill_process(None)
            await update.message.reply_text(result)


@authorized
async def cmd_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Run any shell command. Usage: /cmd <command>"""
    command = " ".join(context.args) if context.args else ""
    if not command:
        await update.message.reply_text("Usage: /cmd <shell command>")
        return

    approved = await guard.request_permission(
        context.bot, update.effective_chat.id,
        "run_script",
        f"Run command: {command}",
        command,
    )
    if not approved:
        return

    running = await update.message.reply_text(f"Running: {command[:100]}...")
    returncode, stdout, stderr = await process_ops.run_command(command, timeout=120)

    # Add to history
    output_preview = stdout.strip() or stderr.strip()
    process_ops.add_to_history(command, returncode, output_preview)

    output = stdout.strip() or "(no output)"
    if returncode != 0:
        err = stderr.strip()
        if err:
            output += f"\n\nSTDERR:\n{err}"
        output = f"EXIT CODE: {returncode}\n\n{output}"

    if len(output) > MAX_MSG - 50:
        output = output[:MAX_MSG - 50] + "\n... (truncated)"

    try:
        await running.edit_text(output)
    except Exception:
        await reply(update.message, output)


@authorized
async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show command history."""
    result = process_ops.get_history()
    await update.message.reply_text(result)


# ── Notebook commands ────────────────────────────────────────────

@authorized
async def cmd_nb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Notebook commands. Usage: /nb <subcommand> [args]"""
    args = context.args or []
    if not args:
        await update.message.reply_text(
            "Notebook commands:\n\n"
            "  /nb open <path>  - Load a .ipynb file\n"
            "  /nb run          - Run all cells\n"
            "  /nb run 5        - Run cell 5\n"
            "  /nb run 5+       - Run from cell 5 onward\n"
            "  /nb cell 5       - Show cell 5 source\n"
            "  /nb edit 5       - Edit cell (send new content next)\n"
            "  /nb out 5        - Show cell 5 output\n"
            "  /nb status       - Kernel status\n"
            "  /nb stop         - Shutdown kernel\n"
            "  /nb env <path>   - Set Python env for kernel\n"
        )
        return

    subcmd = args[0].lower()
    rest = args[1:]

    if subcmd == "open":
        if not rest:
            await update.message.reply_text("Usage: /nb open <path/to/notebook.ipynb>")
            return
        nb_path = " ".join(rest)
        msg = await update.message.reply_text(f"Loading {nb_path}...")
        result = await notebook_mgr.open_notebook(nb_path)
        try:
            await msg.edit_text(result)
        except Exception:
            await update.message.reply_text(result)
        # Show cell list
        cell_list = notebook_mgr.get_cell_list()
        await reply(update.message, cell_list)

    elif subcmd == "run":
        if not rest:
            # Run all cells
            approved = await guard.request_permission(
                context.bot, update.effective_chat.id,
                "run_script",
                "Run all notebook cells",
            )
            if not approved:
                return

            # Start kernel if not running
            if not notebook_mgr.is_running:
                start_msg = await update.message.reply_text("Starting kernel...")
                start_result = await notebook_mgr.start_kernel()
                try:
                    await start_msg.edit_text(start_result)
                except Exception:
                    await update.message.reply_text(start_result)
                if "Failed" in start_result or "not installed" in start_result:
                    return

            status_msg = await update.message.reply_text("Running all cells...")

            async def run_callback(cell_idx, output, success):
                status = "OK" if success else "ERROR"
                text = f"[Cell {cell_idx + 1} - {status}]\n{output}"
                if len(text) > MAX_MSG - 20:
                    text = text[:MAX_MSG - 20] + "\n..."
                try:
                    await context.bot.send_message(update.effective_chat.id, text)
                except Exception as e:
                    logger.warning(f"Notebook callback error: {e}")

            results = await notebook_mgr.run_all(callback=run_callback)

            # Summary
            total = len(results)
            ok_count = sum(1 for _, _, ok in results if ok)
            fail_count = total - ok_count
            summary = f"Notebook run complete: {ok_count}/{total} cells OK"
            if fail_count:
                summary += f", {fail_count} failed"
            try:
                await status_msg.edit_text(summary)
            except Exception:
                await update.message.reply_text(summary)

        else:
            cell_spec = rest[0]

            if cell_spec.endswith("+"):
                # Run from cell N onward
                try:
                    start_idx = int(cell_spec[:-1]) - 1  # convert 1-based to 0-based
                except ValueError:
                    await update.message.reply_text("Usage: /nb run 5+ (run from cell 5 onward)")
                    return

                approved = await guard.request_permission(
                    context.bot, update.effective_chat.id,
                    "run_script",
                    f"Run notebook cells from {start_idx + 1} onward",
                )
                if not approved:
                    return

                if not notebook_mgr.is_running:
                    start_result = await notebook_mgr.start_kernel()
                    await update.message.reply_text(start_result)
                    if "Failed" in start_result or "not installed" in start_result:
                        return

                async def run_from_callback(cell_idx, output, success):
                    status = "OK" if success else "ERROR"
                    text = f"[Cell {cell_idx + 1} - {status}]\n{output}"
                    if len(text) > MAX_MSG - 20:
                        text = text[:MAX_MSG - 20] + "\n..."
                    try:
                        await context.bot.send_message(update.effective_chat.id, text)
                    except Exception as e:
                        logger.warning(f"Notebook callback error: {e}")

                results = await notebook_mgr.run_from(start_idx, callback=run_from_callback)
                total = len(results)
                ok_count = sum(1 for _, _, ok in results if ok)
                await update.message.reply_text(
                    f"Run complete: {ok_count}/{total} cells OK (from cell {start_idx + 1})"
                )

            else:
                # Run a single cell
                try:
                    cell_idx = int(cell_spec) - 1  # convert 1-based to 0-based
                except ValueError:
                    await update.message.reply_text("Usage: /nb run <cell_number>")
                    return

                approved = await guard.request_permission(
                    context.bot, update.effective_chat.id,
                    "run_script",
                    f"Run notebook cell {cell_idx + 1}",
                )
                if not approved:
                    return

                if not notebook_mgr.is_running:
                    start_result = await notebook_mgr.start_kernel()
                    await update.message.reply_text(start_result)
                    if "Failed" in start_result or "not installed" in start_result:
                        return

                running_msg = await update.message.reply_text(f"Running cell {cell_idx + 1}...")
                output = await notebook_mgr.run_cell(cell_idx)
                if len(output) > MAX_MSG - 20:
                    output = output[:MAX_MSG - 20] + "\n..."
                try:
                    await running_msg.edit_text(output)
                except Exception:
                    await reply(update.message, output)

    elif subcmd == "cell":
        if not rest:
            # Show all cells list
            result = notebook_mgr.get_cell_list()
            await reply(update.message, result)
            return
        try:
            cell_idx = int(rest[0]) - 1
        except ValueError:
            await update.message.reply_text("Usage: /nb cell <number>")
            return
        result = notebook_mgr.get_cell_source(cell_idx)
        await reply(update.message, result)

    elif subcmd == "edit":
        if not rest:
            await update.message.reply_text("Usage: /nb edit <cell_number>\nThen send the new content as your next message.")
            return
        try:
            cell_idx = int(rest[0]) - 1
        except ValueError:
            await update.message.reply_text("Usage: /nb edit <cell_number>")
            return
        # Show current content
        current = notebook_mgr.get_cell_source(cell_idx)
        await reply(update.message, f"Current content:\n{current}\n\nSend new content now (your next message will replace this cell).")
        context.chat_data["nb_edit_cell"] = cell_idx

    elif subcmd == "out":
        if not rest:
            await update.message.reply_text("Usage: /nb out <cell_number>")
            return
        try:
            cell_idx = int(rest[0]) - 1
        except ValueError:
            await update.message.reply_text("Usage: /nb out <cell_number>")
            return
        result = notebook_mgr.get_cell_output(cell_idx)
        await reply(update.message, result)

    elif subcmd == "status":
        is_running = notebook_mgr.is_running
        nb_name = notebook_mgr.notebook_name
        env = notebook_mgr._current_env or "none"
        status = "running" if is_running else "stopped"
        await update.message.reply_text(
            f"Notebook: {nb_name}\n"
            f"Kernel: {status}\n"
            f"Python: {env}"
        )

    elif subcmd == "stop":
        result = await notebook_mgr.stop_kernel()
        await update.message.reply_text(result)

    elif subcmd == "env":
        if not rest:
            await update.message.reply_text("Usage: /nb env <path/to/python>\nExample: /nb env /mnt/d/commonenv/Scripts/python.exe")
            return
        python_path = " ".join(rest)
        msg = await update.message.reply_text(f"Starting kernel with {python_path}...")
        result = await notebook_mgr.start_kernel(python_path)
        try:
            await msg.edit_text(result)
        except Exception:
            await update.message.reply_text(result)

    else:
        await update.message.reply_text(f"Unknown notebook subcommand: {subcmd}\nUse /nb for help.")


# ── Git commands ─────────────────────────────────────────────────

@authorized
async def cmd_commit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Git commit. Usage: /commit or /commit <message>"""
    message = " ".join(context.args) if context.args else ""
    repo = _resolve_repo_path(context)

    if not await git_ops.is_git_repo(repo):
        await update.message.reply_text(f"Not a git repo: {repo}\nTip: set last_file_path by using /read on a file in a repo.")
        return

    # Show status first
    status = await git_ops.git_status(repo)
    if "clean" in status.lower() and "nothing" in status.lower():
        await update.message.reply_text(f"[{repo}]\n{status}")
        return

    if not message:
        # Show diff and auto-generate message
        diff = await git_ops.git_diff(repo)
        await reply(update.message, f"Changes in {repo}:\n\n{status}\n\n{diff}")

        generating = await update.message.reply_text("Generating commit message...")
        prompt = (
            f"Generate a short git commit message (one line, max 72 chars) for these changes:\n\n"
            f"Status:\n{status}\n\nDiff:\n{diff[:2000]}"
        )
        message = await llm.ask(prompt, system_prompt="You are a git commit message writer. Output only the commit message, no quotes, no explanation.")
        message = message.strip().strip('"').strip("'").split("\n")[0][:72]
        try:
            await generating.edit_text(f"Auto-generated message: {message}")
        except Exception:
            await update.message.reply_text(f"Auto-generated message: {message}")

    approved = await guard.request_permission(
        context.bot, update.effective_chat.id,
        "run_script",
        f"Git commit in {repo}",
        f"Message: {message}\n\n{status}",
    )
    if approved:
        result = await git_ops.git_commit(repo, message)
        await update.message.reply_text(result)


@authorized
async def cmd_diff(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show git diff."""
    repo = _resolve_repo_path(context)
    if not await git_ops.is_git_repo(repo):
        await update.message.reply_text(f"Not a git repo: {repo}")
        return

    # Show both unstaged and staged
    unstaged = await git_ops.git_diff(repo, staged=False)
    staged = await git_ops.git_diff(repo, staged=True)
    result = f"[{repo}]\n\nUnstaged:\n{unstaged}\n\nStaged:\n{staged}"
    await reply(update.message, result)


@authorized
async def cmd_gitlog(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show recent commits. Usage: /gitlog or /gitlog 20"""
    count = 10
    if context.args:
        try:
            count = int(context.args[0])
        except ValueError:
            pass
    repo = _resolve_repo_path(context)
    if not await git_ops.is_git_repo(repo):
        await update.message.reply_text(f"Not a git repo: {repo}")
        return
    result = await git_ops.git_log(repo, count)
    await reply(update.message, f"[{repo}] Last {count} commits:\n\n{result}")


@authorized
async def cmd_undo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Undo last commit (soft reset). Needs approval."""
    repo = _resolve_repo_path(context)
    if not await git_ops.is_git_repo(repo):
        await update.message.reply_text(f"Not a git repo: {repo}")
        return

    # Show what will be undone
    log = await git_ops.git_log(repo, 1)
    approved = await guard.request_permission(
        context.bot, update.effective_chat.id,
        "run_script",
        f"Undo last commit in {repo}",
        f"Will undo: {log}",
    )
    if approved:
        result = await git_ops.git_undo_last(repo)
        await update.message.reply_text(result)


# ── Git auto-commit helper ───────────────────────────────────────

async def _offer_git_commit(update: Update, context: ContextTypes.DEFAULT_TYPE, filepath: str, description: str):
    """After approved file writes, offer to commit if inside a git repo."""
    try:
        p = Path(filepath)
        check = p if p.is_dir() else p.parent
        repo_path = None
        while check != check.parent:
            if (check / ".git").exists():
                repo_path = str(check)
                break
            check = check.parent

        if repo_path:
            await update.message.reply_text(
                f"File is in git repo ({repo_path}).\n"
                f"Use /commit to commit changes, or /diff to review."
            )
    except Exception:
        pass


# ── Quality of life commands ─────────────────────────────────────

@authorized
async def cmd_open(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Open file/folder in Windows Explorer. Usage: /open <path>"""
    filepath = " ".join(context.args) if context.args else str(Config.WORKSPACE)
    win_path = _wsl_to_win(filepath)
    try:
        cmd = f"powershell.exe -Command \"Start-Process '{win_path}'\""
        await process_ops.run_command(cmd, timeout=10)
        await update.message.reply_text(f"Opened: {win_path}")
    except Exception as e:
        await update.message.reply_text(f"Failed to open: {e}")


@authorized
async def cmd_clip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Copy text to Windows clipboard. Usage: /clip <text>"""
    text = " ".join(context.args) if context.args else ""
    if not text:
        await update.message.reply_text("Usage: /clip <text to copy>")
        return
    try:
        # Use echo with pipe to clip.exe
        escaped = text.replace("'", "'\\''")
        cmd = f"echo '{escaped}' | clip.exe"
        returncode, _, stderr = await process_ops.run_command(cmd, timeout=10)
        if returncode == 0:
            preview = text[:100] + ("..." if len(text) > 100 else "")
            await update.message.reply_text(f"Copied to clipboard: {preview}")
        else:
            await update.message.reply_text(f"Clipboard error: {stderr}")
    except Exception as e:
        await update.message.reply_text(f"Failed: {e}")


@authorized
async def cmd_bookmark(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Save a bookmark. Usage: /bookmark <name> <path>"""
    args = context.args or []
    if len(args) < 2:
        # Show existing bookmarks
        bookmarks = context.bot_data.get("bookmarks", {})
        if not bookmarks:
            await update.message.reply_text("No bookmarks saved.\nUsage: /bookmark <name> <path>")
            return
        lines = ["Bookmarks:\n"]
        for name, path in bookmarks.items():
            lines.append(f"  {name} -> {path}")
        await update.message.reply_text("\n".join(lines))
        return

    name = args[0]
    path = " ".join(args[1:])

    if "bookmarks" not in context.bot_data:
        context.bot_data["bookmarks"] = {}

    context.bot_data["bookmarks"][name] = path
    await update.message.reply_text(f"Bookmark saved: {name} -> {path}")


@authorized
async def cmd_go(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List directory of a bookmark. Usage: /go <name>"""
    name = " ".join(context.args) if context.args else ""
    if not name:
        # List all bookmarks
        bookmarks = context.bot_data.get("bookmarks", {})
        if not bookmarks:
            await update.message.reply_text("No bookmarks. Use /bookmark <name> <path> to create one.")
            return
        lines = ["Bookmarks:\n"]
        for bname, bpath in bookmarks.items():
            lines.append(f"  /go {bname} -> {bpath}")
        await update.message.reply_text("\n".join(lines))
        return

    bookmarks = context.bot_data.get("bookmarks", {})
    if name not in bookmarks:
        await update.message.reply_text(f"Bookmark '{name}' not found.\nUse /bookmark to see all bookmarks.")
        return

    path = bookmarks[name]
    content = file_ops.list_dir(path)
    await reply(update.message, f"{name} ({path}):\n\n{content}")


# ── GPU & Reminders ───────────────────────────────────────────────

@authorized
async def cmd_gpu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show GPU status (NVIDIA). Usage: /gpu"""
    await typing(update)
    # Try nvidia-smi on Windows via PowerShell
    ps_cmd = (
        'powershell.exe -NoProfile -Command "'
        'nvidia-smi --query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu '
        '--format=csv,noheader,nounits"'
    )
    returncode, stdout, stderr = await process_ops.run_command(ps_cmd, timeout=10)
    if returncode != 0 or not stdout.strip():
        await update.message.reply_text(
            "[ GPU STATUS ]\n━━━━━━━━━━━━━━\n\n"
            "nvidia-smi not available or no NVIDIA GPU detected."
        )
        return

    lines = ["[ GPU STATUS ]", "━━━━━━━━━━━━━━", ""]
    for i, row in enumerate(stdout.strip().split("\n")):
        parts = [p.strip() for p in row.split(",")]
        if len(parts) >= 5:
            name, util, mem_used, mem_total, temp = parts[:5]
            mem_pct = int(float(mem_used) / float(mem_total) * 100) if float(mem_total) > 0 else 0
            bar_len = 15
            filled = int(bar_len * mem_pct / 100)
            mem_bar = "█" * filled + "░" * (bar_len - filled)
            lines.append(f"GPU {i}: {name}")
            lines.append(f"  Load:  {util}%")
            lines.append(f"  VRAM:  [{mem_bar}] {mem_used}/{mem_total} MB ({mem_pct}%)")
            lines.append(f"  Temp:  {temp}°C")
        else:
            lines.append(row)

    await update.message.reply_text("\n".join(lines))


@authorized
async def cmd_remind(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set a reminder. Usage: /remind <minutes> <message>"""
    args = context.args
    if not args or len(args) < 2:
        await update.message.reply_text("Usage: /remind <minutes> <message>\nExample: /remind 30 check training")
        return

    try:
        minutes = int(args[0])
    except ValueError:
        await update.message.reply_text("First argument must be minutes (number).")
        return

    message = " ".join(args[1:])

    async def _fire_reminder():
        await asyncio.sleep(minutes * 60)
        try:
            await context.bot.send_message(
                Config.TELEGRAM_CHAT_ID,
                f"[ REMINDER ]\n━━━━━━━━━━━━\n\n{message}\n\n(set {minutes}m ago)"
            )
        except Exception as e:
            logger.error(f"Reminder failed: {e}")

    task = asyncio.create_task(_fire_reminder())
    _reminders.append({"task": task, "minutes": minutes, "message": message})

    await update.message.reply_text(f"Reminder set, sir. I'll notify you in {minutes} minutes.")


# ── System commands ──────────────────────────────────────────────

_PLAN_SYSTEM_PROMPT = """You are a command planner for a WSL2 (Ubuntu) agent that controls a Windows machine.

ENVIRONMENT:
- Running in WSL2 on Ubuntu, Windows drives at /mnt/c/ and /mnt/d/
- Ollama LLM running on Windows at {ollama_url}
- Can call powershell.exe for Windows tasks (open browser, etc.)

AVAILABLE DIRECTIVES (put each on its own line):
- SCREENSHOT — takes a screenshot and sends it (waits for page to load)
- SENDFILE:<wsl-path> — sends a file to the user (not SENDERFILE)
- OPENDEV — auto-detects the dev server port, opens it in browser, waits for load

CRITICAL CROSS-PLATFORM RULES:
- Projects with node_modules installed on Windows CANNOT run npm/node from WSL.
  ALWAYS use powershell.exe for Node.js projects:
  powershell.exe -Command "cd 'D:\\projects\\myapp'; npm run dev"

- Python venvs created on Windows have Scripts/ not bin/, and have CRLF line endings.
  Do NOT source them from bash. Use the exe directly:
  /mnt/d/myenv/Scripts/python.exe script.py

RULES:
- Output ONLY a bash script. No explanations, no markdown fences.
- The script runs as a SINGLE bash script, so cd and env vars persist.
- For dev servers that run forever, start via powershell and background it:
  powershell.exe -Command "cd 'D:\\path'; npm run dev" &
  sleep 15
- NEVER guess or hardcode a port number. Use OPENDEV to auto-detect the port.
- After OPENDEV, use SCREENSHOT to capture the page.
- Keep it minimal — only the commands needed.
"""


def _extract_url_from_output(text: str) -> str | None:
    """Extract a local dev server URL from captured process output.
    Looks for 'Local: http://...' or 'http://localhost:...' patterns,
    ignoring github/npm/external URLs."""
    _IGNORE = ("github.com", "npmjs.com", "npm.community", "nodejs.org", "vitejs.dev")
    # Strip ANSI escape codes
    clean = re.sub(r"\x1b\[[0-9;]*m", "", text)
    # First try to find "Local:" line (Vite/Next.js format)
    local_match = re.search(r"Local:\s*(https?://[^\s]+)", clean)
    if local_match:
        return local_match.group(1).strip()
    # Then try any localhost URL
    for m in re.finditer(r"https?://localhost[:\d]*/?\S*", clean):
        url = m.group(0).rstrip(".,;)")
        if not any(h in url for h in _IGNORE):
            return url
    # Then any 127.0.0.1 URL
    for m in re.finditer(r"https?://127\.0\.0\.1[:\d]*/?\S*", clean):
        return m.group(0).rstrip(".,;)")
    return None


async def _find_dev_server_by_scan() -> str | None:
    """Fallback: scan common ports via PowerShell to find an HTTP server."""
    ports = [8080, 8081, 5173, 3000, 8000, 4200, 5000, 5500, 3001, 4000, 8888, 1234]

    # Write a PS1 script that checks ports with HTTP GET (not just TCP)
    # This distinguishes web servers from databases
    ps_lines = [
        "foreach ($p in @(" + ",".join(str(p) for p in ports) + ")) {",
        "  try {",
        "    $r = Invoke-WebRequest -Uri \"http://127.0.0.1:$p\" -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop",
        "    if ($r.StatusCode -lt 500) { Write-Output $p; exit }",
        "  } catch {",
        "    if ($_.Exception.Response -ne $null) { Write-Output $p; exit }",
        "  }",
        "}",
    ]
    script_path = Config.WORKSPACE / "_portscan.ps1"
    script_path.write_text("\n".join(ps_lines), encoding="utf-8")

    wsl_path = str(script_path)
    if wsl_path.startswith("/mnt/"):
        parts = wsl_path[5:].split("/", 1)
        win_path = f"{parts[0].upper()}:\\{parts[1]}".replace("/", "\\")
    else:
        win_path = wsl_path

    try:
        cmd = f'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{win_path}"'
        returncode, stdout, stderr = await process_ops.run_command(cmd, timeout=30)
        port = stdout.strip()
        if port and port.isdigit():
            return f"http://localhost:{port}"
    finally:
        try:
            script_path.unlink()
        except Exception:
            pass

    return None


async def _execute_task(bot, chat_id, task_text: str, context):
    """Use LLM to plan steps, get approval, execute, report results."""
    # Step 1: Ask LLM to create a plan
    planning_msg = await bot.send_message(chat_id, "Planning task steps...")

    system = _PLAN_SYSTEM_PROMPT.format(ollama_url=Config.OLLAMA_URL)
    plan = await llm.ask(task_text, system_prompt=system)

    if not plan or plan.startswith("Cannot connect") or plan.startswith("Ollama error"):
        await planning_msg.edit_text(f"Failed to plan: {plan}")
        return

    # Clean up: remove markdown fences if LLM wrapped them
    clean_plan = plan.strip()
    if clean_plan.startswith("```"):
        lines = clean_plan.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        clean_plan = "\n".join(lines)

    await planning_msg.edit_text(f"PLAN:\n\n{clean_plan}")

    # Step 2: Get approval
    approved = await guard.request_permission(
        bot, chat_id,
        "run_script",
        "Execute multi-step task",
        clean_plan[:500],
    )
    if not approved:
        return

    # Step 3: Split plan into bash segments and special directives
    # Everything between SCREENSHOT/SENDFILE lines is one bash segment
    status_msg = await bot.send_message(chat_id, "Executing...")

    segments = []  # ("bash"|"screenshot"|"sendfile"|"opendev", data)
    current_bash = []

    for line in clean_plan.strip().split("\n"):
        stripped = line.strip()
        if stripped == "SCREENSHOT":
            if current_bash:
                segments.append(("bash", "\n".join(current_bash)))
                current_bash = []
            segments.append(("screenshot", ""))
        elif stripped == "OPENDEV":
            if current_bash:
                segments.append(("bash", "\n".join(current_bash)))
                current_bash = []
            segments.append(("opendev", ""))
        elif stripped.startswith("SENDFILE:") or stripped.startswith("SENDERFILE:"):
            if current_bash:
                segments.append(("bash", "\n".join(current_bash)))
                current_bash = []
            path = stripped.split(":", 1)[1].strip()
            segments.append(("sendfile", path))
        else:
            current_bash.append(line)

    if current_bash:
        segments.append(("bash", "\n".join(current_bash)))

    output_log = ""
    bg_procs = []
    had_error = False
    detected_url = None
    bg_captured_output = ""  # output from background processes for URL extraction

    for seg_type, seg_data in segments:
        if seg_type == "screenshot":
            try:
                await status_msg.edit_text("Taking screenshot...")
            except Exception:
                pass
            import asyncio as _aio
            await _aio.sleep(8)  # let page fully render
            path, error = await screenshot.take_screenshot()
            if error:
                output_log += f"\nScreenshot error: {error}"
            elif path:
                with open(path, "rb") as f:
                    await bot.send_photo(chat_id, photo=f, caption="Screenshot")
                output_log += "\nScreenshot sent."

        elif seg_type == "sendfile":
            real_path, error = file_ops.get_file_for_send(seg_data)
            if error:
                output_log += f"\nSend file error ({seg_data}): {error}"
            elif real_path:
                with open(real_path, "rb") as f:
                    await bot.send_document(chat_id, document=f, filename=Path(real_path).name)
                output_log += f"\nSent: {seg_data}"

        elif seg_type == "opendev":
            try:
                await status_msg.edit_text("Detecting dev server URL...")
            except Exception:
                pass

            # 1. Try to extract URL from background process output
            found_url = _extract_url_from_output(bg_captured_output)
            if found_url:
                output_log += f"\nURL from server output: {found_url}"

            # 2. Fallback: scan ports with HTTP check
            if not found_url:
                try:
                    await status_msg.edit_text("Scanning ports for HTTP server...")
                except Exception:
                    pass
                found_url = await _find_dev_server_by_scan()

            if found_url:
                detected_url = found_url
                output_log += f"\nDev server found: {detected_url}"
                open_cmd = f"powershell.exe -Command \"Start-Process '{detected_url}'\""
                await process_ops.run_command(open_cmd, timeout=10)
                output_log += f"\nOpened {detected_url} in browser"
                import asyncio as _aio
                await _aio.sleep(12)  # let page fully load and render
            else:
                output_log += "\nCould not find a running dev server."
                await bot.send_message(chat_id, "Could not detect dev server. Check if it started correctly.")

        elif seg_type == "bash":
            try:
                await status_msg.edit_text(f"Running bash segment...\n{seg_data[:200]}")
            except Exception:
                pass

            has_bg = any(l.strip().endswith("&") for l in seg_data.split("\n"))

            if has_bg:
                # Run as background process
                proc, captured = await process_ops.run_background_command(
                    f"bash -c {_shell_quote(seg_data)}",
                    wait_for="http",
                    wait_timeout=25,
                )
                bg_procs.append(proc)
                bg_captured_output += captured
                output_log += f"\n{captured}"
            else:
                # Run as a single bash script
                returncode, stdout, stderr = await process_ops.run_command(
                    f"bash -c {_shell_quote(seg_data)}",
                    timeout=120,
                )
                if stdout.strip():
                    output_log += f"\n{stdout.strip()[:1000]}"
                if returncode != 0:
                    output_log += f"\nERROR (exit {returncode}): {stderr.strip()[:500]}"
                    had_error = True
                    break

    # Step 4: Report results
    result_header = "TASK COMPLETED" if not had_error else "TASK FAILED"
    result_text = f"{result_header}\n\n{output_log[-3000:]}"
    try:
        await status_msg.edit_text(result_text)
    except Exception:
        await bot.send_message(chat_id, result_text)

    # Clean up background processes
    for proc in bg_procs:
        try:
            proc.terminate()
        except Exception:
            pass


def _shell_quote(script: str) -> str:
    """Quote a multi-line script for bash -c '...'"""
    import shlex
    return shlex.quote(script)


@authorized
async def cmd_do(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Execute a complex multi-step task."""
    task = " ".join(context.args) if context.args else ""
    if not task:
        await update.message.reply_text("Usage: /do <describe what you want done>")
        return
    await _execute_task(context.bot, update.effective_chat.id, task, context)


@authorized
async def cmd_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("Capturing screenshot...")
    path, error = await screenshot.take_screenshot()
    if error:
        await msg.edit_text(f"Screenshot error: {error}")
        return
    try:
        await msg.delete()
    except Exception:
        pass
    with open(path, "rb") as f:
        await update.message.reply_photo(photo=f, caption="Screenshot")


@authorized
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status = await system_info.get_status()
    await update.message.reply_text(status)


@authorized
async def cmd_models(update: Update, context: ContextTypes.DEFAULT_TYPE):
    models = await system_info.get_models()
    await update.message.reply_text(models)


@authorized
async def cmd_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log_path = Config.WORKSPACE / Config.LOG_FILE
    if not log_path.exists():
        await update.message.reply_text("No logs yet.")
        return
    try:
        content = log_path.read_text(encoding="utf-8", errors="replace")
        tail = content[-2000:] if len(content) > 2000 else content
        await update.message.reply_text(f"Recent Logs:\n\n{tail}")
    except Exception as e:
        await update.message.reply_text(f"Error reading logs: {e}")


@authorized
async def cmd_apply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Apply a pending LLM-suggested fix."""
    pending = context.chat_data.get("pending_fix")
    if not pending:
        await update.message.reply_text("No pending fix to apply.")
        return

    script = pending["script"]
    fix_code = pending["fix"]

    # Extract code from markdown code blocks if present
    code_blocks = re.findall(r"```(?:python)?\n?(.*?)```", fix_code, re.DOTALL)
    if code_blocks:
        fix_code = code_blocks[0].strip()

    approved = await guard.request_permission(
        context.bot, update.effective_chat.id,
        "apply_fix",
        f"Apply LLM fix to {script}",
        fix_code[:300],
    )
    if approved:
        result = file_ops.write_file(script, fix_code)
        await update.message.reply_text(f"{result}\n\nUse /run {script} to test.")
        context.chat_data.pop("pending_fix", None)


# ── Free text handler ─────────────────────────────────────────────

@authorized
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    text = update.message.text

    # Check if we're in notebook edit mode
    nb_edit_cell = context.chat_data.get("nb_edit_cell")
    if nb_edit_cell is not None:
        cell_idx = nb_edit_cell
        context.chat_data.pop("nb_edit_cell", None)
        result = notebook_mgr.edit_cell(cell_idx, text)
        await update.message.reply_text(result)
        return

    intent, args = brain.classify(text)

    if intent == "multi_step":
        await _execute_task(context.bot, update.effective_chat.id, text, context)

    elif intent == "ask_llm":
        await typing(update)
        thinking = await update.message.reply_text("Processing, sir...")
        response = await llm.ask(text, system_prompt=JARVIS_SYSTEM)
        try:
            await thinking.edit_text(response or "No response.")
        except Exception:
            await reply(update.message, response or "No response.")

    elif intent == "screenshot":
        await cmd_screenshot(update, context)
    elif intent == "status":
        await cmd_status(update, context)
    elif intent == "models":
        await cmd_models(update, context)
    elif intent == "logs":
        await cmd_logs(update, context)

    elif intent == "read_file":
        context.chat_data["last_file_path"] = args
        content = file_ops.read_file(args)
        await reply(update.message, f"{args}:\n\n{content}")

    elif intent == "list_dir":
        content = file_ops.list_dir(args)
        await reply(update.message, f"{args}:\n\n{content}")

    elif intent == "run_script":
        approved = await guard.request_permission(
            context.bot, update.effective_chat.id,
            "run_script",
            f"Run: {args}",
        )
        if approved:
            await monitor.run_and_report(context.bot, update.effective_chat.id, args)

    elif intent == "send_file":
        await _handle_send_file(update, args)

    elif intent in ("write_file", "delete_file"):
        cmd = intent.split("_")[0]
        await update.message.reply_text(
            f"Use /{cmd} command for this. Example:\n/{cmd} <path>"
        )

    elif intent == "fix_error":
        last_err = monitor.get_last_error(update.effective_chat.id)
        if last_err:
            thinking = await update.message.reply_text("Analyzing error with LLM...")
            prompt = (
                f"The following Python script failed:\n"
                f"Script: {last_err['script']}\n"
                f"Error:\n{last_err['error']}\n"
                f"Output:\n{last_err['stdout']}\n\n"
                f"Diagnose the problem and suggest a specific fix."
            )
            response = await llm.ask_code(prompt)
            try:
                await thinking.edit_text(f"Diagnosis:\n\n{response}")
            except Exception:
                await reply(update.message, f"Diagnosis:\n\n{response}")
        else:
            await update.message.reply_text("No recent errors to diagnose.")

    else:
        await typing(update)
        thinking = await update.message.reply_text("Processing, sir...")
        response = await llm.ask(text, system_prompt=JARVIS_SYSTEM)
        try:
            await thinking.edit_text(response or "No response.")
        except Exception:
            await reply(update.message, response or "No response.")


# ── Document handler (receive files from user) ───────────────────

@authorized
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Save files sent by user to workspace (with permission)."""
    doc = update.message.document
    if not doc:
        return

    filename = doc.file_name or "received_file"
    filepath = Config.WORKSPACE / filename

    approved = await guard.request_permission(
        context.bot, update.effective_chat.id,
        "save_file",
        f"Save received file: {filename}",
        f"Size: {doc.file_size} bytes -> {filepath}",
    )
    if approved:
        tg_file = await context.bot.get_file(doc.file_id)
        await tg_file.download_to_drive(str(filepath))
        await update.message.reply_text(f"Saved to {filepath}")


# ── Callback handler (inline buttons) ────────────────────────────

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.data:
        return

    # Auth check
    if update.effective_chat.id != Config.TELEGRAM_CHAT_ID:
        return

    # Permission callbacks
    if query.data.startswith("perm:"):
        await guard.handle_callback(update, context)
        return

    # Fix callbacks
    if query.data.startswith("fix:"):
        parts = query.data.split(":")
        fix_type = parts[1] if len(parts) > 1 else ""
        chat_id = int(parts[2]) if len(parts) > 2 else update.effective_chat.id

        if fix_type == "llm":
            await query.answer("Analyzing with LLM...")
            await query.edit_message_reply_markup(None)

            last_err = monitor.get_last_error(chat_id)
            if not last_err:
                await context.bot.send_message(chat_id, "No recent error found.")
                return

            msg = await context.bot.send_message(chat_id, "LLM is analyzing the error...")

            script_content = file_ops.read_file(last_err["script"], max_chars=5000)
            prompt = (
                f"Fix this Python script error.\n\n"
                f"Script ({last_err['script']}):\n{script_content}\n\n"
                f"Error:\n{last_err['error']}\n\n"
                f"Output:\n{last_err['stdout']}\n\n"
                f"Provide the corrected full script code. Wrap it in ```python``` markers."
            )
            fix = await llm.ask_code(prompt)

            try:
                await msg.edit_text(
                    f"Suggested fix for {last_err['script']}:\n\n{fix}\n\n"
                    f"Send /apply to apply this fix."
                )
            except Exception:
                await context.bot.send_message(
                    chat_id,
                    f"Suggested fix for {last_err['script']}:\n\n{fix}\n\n"
                    f"Send /apply to apply this fix.",
                )

            context.chat_data["pending_fix"] = {
                "script": last_err["script"],
                "fix": fix,
            }
            monitor.clear_error(chat_id)

        elif fix_type == "manual":
            await query.answer("OK, waiting for your fix.")
            await query.edit_message_reply_markup(None)
            await context.bot.send_message(
                chat_id,
                "Send me the fix as text, or use /write to update the file directly.",
            )


# ── Bot initialization ────────────────────────────────────────────

async def post_init(application: Application):
    """Register bot commands menu and send startup notification."""
    commands = [
        BotCommand("start", "Start the agent"),
        BotCommand("help", "Show all commands"),
        BotCommand("ask", "Ask LLM"),
        BotCommand("code", "Ask code model"),
        BotCommand("run", "Run a Python script"),
        BotCommand("read", "Read a file"),
        BotCommand("write", "Write to a file"),
        BotCommand("delete", "Delete a file"),
        BotCommand("send", "Send a file to you"),
        BotCommand("ls", "List directory"),
        BotCommand("find", "Search for files"),
        BotCommand("tree", "Show directory tree"),
        BotCommand("recent", "Recently modified files"),
        BotCommand("watch", "Run script with live output"),
        BotCommand("cmd", "Run shell command"),
        BotCommand("tail", "Monitor a log file"),
        BotCommand("procs", "Show background processes"),
        BotCommand("kill", "Kill background process"),
        BotCommand("history", "Command history"),
        BotCommand("nb", "Notebook commands"),
        BotCommand("commit", "Git commit"),
        BotCommand("diff", "Show git diff"),
        BotCommand("gitlog", "Recent git commits"),
        BotCommand("undo", "Undo last commit"),
        BotCommand("screenshot", "Capture screen"),
        BotCommand("status", "System status"),
        BotCommand("models", "List Ollama models"),
        BotCommand("logs", "View agent logs"),
        BotCommand("apply", "Apply LLM-suggested fix"),
        BotCommand("do", "Execute a complex task"),
        BotCommand("open", "Open in Explorer"),
        BotCommand("clip", "Copy to clipboard"),
        BotCommand("bookmark", "Save a bookmark"),
        BotCommand("go", "Go to bookmark"),
        BotCommand("gpu", "GPU status"),
        BotCommand("remind", "Set a reminder"),
    ]
    await application.bot.set_my_commands(commands)

    # ── Startup Dashboard ──
    try:
        import shutil
        import datetime

        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        health = await llm.is_alive()

        def _dot(ok): return "ON" if ok else "OFF"

        try:
            disk = shutil.disk_usage(str(Config.WORKSPACE))
            disk_free = f"{disk.free // (1024**3)}GB free"
        except Exception:
            disk_free = "N/A"

        # GPU check
        gpu_line = "N/A"
        try:
            rc, out, _ = await process_ops.run_command(
                'powershell.exe -NoProfile -Command "'
                'nvidia-smi --query-gpu=name,memory.used,memory.total '
                '--format=csv,noheader,nounits"',
                timeout=8,
            )
            if rc == 0 and out.strip():
                parts = [p.strip() for p in out.strip().split("\n")[0].split(",")]
                if len(parts) >= 3:
                    gpu_line = f"{parts[0]} ({parts[1]}/{parts[2]} MB)"
        except Exception:
            pass

        dashboard = (
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "   J.A.R.V.I.S.  v2.0\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "\n"
            f"  Time:    {now}\n"
            f"  Groq:    {_dot(health.get('Groq', False))}\n"
            f"  Gemini:  {_dot(health.get('Gemini', False))}\n"
            f"  Ollama:  {_dot(health.get('Ollama', False))}\n"
            f"  Disk:    {disk_free}\n"
            f"  GPU:     {gpu_line}\n"
            "\n"
            "  All systems online, sir.\n"
            "  Send /help for commands.\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )

        await application.bot.send_message(Config.TELEGRAM_CHAT_ID, dashboard)
    except Exception as e:
        logger.error(f"Failed to send startup dashboard: {e}")
        try:
            await application.bot.send_message(
                Config.TELEGRAM_CHAT_ID,
                "J.A.R.V.I.S. online. Send /help for commands.",
            )
        except Exception:
            pass

    logger.info("Bot initialized and commands registered.")


def create_bot() -> Application:
    """Create and configure the Telegram bot."""
    app = (
        Application.builder()
        .token(Config.TELEGRAM_TOKEN)
        .post_init(post_init)
        .concurrent_updates(True)
        .build()
    )

    # Command handlers — existing
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("ask", cmd_ask))
    app.add_handler(CommandHandler("code", cmd_code))
    app.add_handler(CommandHandler("read", cmd_read))
    app.add_handler(CommandHandler("ls", cmd_ls))
    app.add_handler(CommandHandler("write", cmd_write))
    app.add_handler(CommandHandler("delete", cmd_delete))
    app.add_handler(CommandHandler("send", cmd_send))
    app.add_handler(CommandHandler("run", cmd_run))
    app.add_handler(CommandHandler("do", cmd_do))
    app.add_handler(CommandHandler("screenshot", cmd_screenshot))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("models", cmd_models))
    app.add_handler(CommandHandler("logs", cmd_logs))
    app.add_handler(CommandHandler("apply", cmd_apply))

    # Command handlers — file discovery
    app.add_handler(CommandHandler("find", cmd_find))
    app.add_handler(CommandHandler("tree", cmd_tree))
    app.add_handler(CommandHandler("recent", cmd_recent))

    # Command handlers — process management
    app.add_handler(CommandHandler("watch", cmd_watch))
    app.add_handler(CommandHandler("tail", cmd_tail))
    app.add_handler(CommandHandler("procs", cmd_procs))
    app.add_handler(CommandHandler("kill", cmd_kill))
    app.add_handler(CommandHandler("cmd", cmd_cmd))
    app.add_handler(CommandHandler("history", cmd_history))

    # Command handlers — notebook
    app.add_handler(CommandHandler("nb", cmd_nb))

    # Command handlers — git
    app.add_handler(CommandHandler("commit", cmd_commit))
    app.add_handler(CommandHandler("diff", cmd_diff))
    app.add_handler(CommandHandler("gitlog", cmd_gitlog))
    app.add_handler(CommandHandler("undo", cmd_undo))

    # Command handlers — quality of life
    app.add_handler(CommandHandler("open", cmd_open))
    app.add_handler(CommandHandler("clip", cmd_clip))
    app.add_handler(CommandHandler("bookmark", cmd_bookmark))
    app.add_handler(CommandHandler("go", cmd_go))
    app.add_handler(CommandHandler("gpu", cmd_gpu))
    app.add_handler(CommandHandler("remind", cmd_remind))

    # Inline button callback handler
    app.add_handler(CallbackQueryHandler(handle_callback))

    # Free text messages (must be after command handlers)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # File/document receiver
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    return app
