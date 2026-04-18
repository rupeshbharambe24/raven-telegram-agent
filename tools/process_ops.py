import asyncio
import logging
from pathlib import Path

from config import Config

logger = logging.getLogger(__name__)


async def run_script(filepath: str, timeout: int = 120) -> tuple[int, str, str]:
    """Run a Python script asynchronously. Returns (returncode, stdout, stderr)."""
    target = Path(filepath)
    if not target.exists():
        return -1, "", f"File not found: {filepath}"
    if target.suffix != ".py":
        return -1, "", f"Not a Python file: {filepath}"

    try:
        proc = await asyncio.create_subprocess_exec(
            "python3", str(target),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(target.parent),
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return (
            proc.returncode,
            stdout.decode("utf-8", errors="replace"),
            stderr.decode("utf-8", errors="replace"),
        )
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return -1, "", f"Script timed out after {timeout}s"
    except Exception as e:
        return -1, "", str(e)


async def run_command(command: str, timeout: int = 30, cwd: str | None = None) -> tuple[int, str, str]:
    """Run a shell command asynchronously via bash. Returns (returncode, stdout, stderr)."""
    work_dir = cwd or str(Config.WORKSPACE)
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=work_dir,
            executable="/bin/bash",
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return (
            proc.returncode,
            stdout.decode("utf-8", errors="replace"),
            stderr.decode("utf-8", errors="replace"),
        )
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return -1, "", f"Command timed out after {timeout}s"
    except Exception as e:
        return -1, "", str(e)


async def run_background_command(command: str, cwd: str | None = None,
                                  wait_for: str | None = None,
                                  wait_timeout: int = 30) -> tuple[asyncio.subprocess.Process, str]:
    """Start a long-running process in background.
    Optionally wait for a specific string in output (e.g. a URL).
    Returns (process, captured_output)."""
    work_dir = cwd or str(Config.WORKSPACE)
    proc = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=work_dir,
        executable="/bin/bash",
    )

    captured = ""
    if wait_for and proc.stdout:
        try:
            deadline = asyncio.get_event_loop().time() + wait_timeout
            while asyncio.get_event_loop().time() < deadline:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=5)
                if not line:
                    break
                decoded = line.decode("utf-8", errors="replace")
                captured += decoded
                if wait_for.lower() in decoded.lower():
                    # Read a couple more lines to capture full URL info
                    for _ in range(3):
                        try:
                            extra = await asyncio.wait_for(proc.stdout.readline(), timeout=2)
                            if extra:
                                captured += extra.decode("utf-8", errors="replace")
                        except asyncio.TimeoutError:
                            break
                    break
        except asyncio.TimeoutError:
            pass

    return proc, captured


# ── Process tracking ──────────────────────────────────────────────

_tracked_procs: dict[str, dict] = {}  # name -> {proc, command, started, cwd}
_command_history: list[dict] = []     # [{command, time, returncode, output_preview}]
_MAX_HISTORY = 20


def track_process(name: str, proc: asyncio.subprocess.Process, command: str, cwd: str = ""):
    """Register a background process for tracking."""
    import time
    _tracked_procs[name] = {
        "proc": proc,
        "command": command,
        "started": time.time(),
        "cwd": cwd,
    }


def add_to_history(command: str, returncode: int, output: str = ""):
    """Record a command in history."""
    import time
    _command_history.append({
        "command": command,
        "time": time.time(),
        "returncode": returncode,
        "output": output[:200],
    })
    if len(_command_history) > _MAX_HISTORY:
        _command_history.pop(0)


def get_running_procs() -> str:
    """List all tracked background processes."""
    import time
    alive = []
    dead = []
    for name, info in list(_tracked_procs.items()):
        proc = info["proc"]
        if proc.returncode is None:
            elapsed = int(time.time() - info["started"])
            alive.append(f"  [{name}] PID {proc.pid} ({elapsed}s) - {info['command'][:60]}")
        else:
            dead.append(name)

    for name in dead:
        _tracked_procs.pop(name, None)

    if not alive:
        return "No background processes running."
    return "Running processes:\n\n" + "\n".join(alive)


async def kill_process(name: str | None = None) -> str:
    """Kill a tracked process by name, or the most recent one."""
    if not _tracked_procs:
        return "No background processes to kill."

    if name and name in _tracked_procs:
        target_name = name
    elif name == "all":
        killed = []
        for n, info in list(_tracked_procs.items()):
            try:
                info["proc"].terminate()
                killed.append(n)
            except Exception:
                pass
        _tracked_procs.clear()
        return f"Killed {len(killed)} processes: {', '.join(killed)}"
    else:
        # Kill most recent
        target_name = list(_tracked_procs.keys())[-1]

    info = _tracked_procs.pop(target_name)
    try:
        info["proc"].terminate()
        return f"Killed [{target_name}] (PID {info['proc'].pid})"
    except Exception as e:
        return f"Error killing {target_name}: {e}"


def get_history() -> str:
    """Return command history."""
    import time, datetime
    if not _command_history:
        return "No command history."
    lines = ["Command History:\n"]
    for entry in reversed(_command_history):
        dt = datetime.datetime.fromtimestamp(entry["time"])
        time_str = dt.strftime("%H:%M:%S")
        status = "OK" if entry["returncode"] == 0 else f"ERR({entry['returncode']})"
        lines.append(f"  {time_str} [{status}] {entry['command'][:70]}")
    return "\n".join(lines)


async def watch_script(filepath: str, python_path: str = "python3",
                       callback=None, timeout: int = 600) -> tuple[int, str]:
    """Run a script and stream output via callback(chunk).
    callback is an async function that receives output chunks.
    Returns (returncode, full_output)."""
    target = Path(filepath)
    if not target.exists():
        return -1, f"File not found: {filepath}"

    proc = await asyncio.create_subprocess_exec(
        python_path, str(target),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=str(target.parent),
    )
    track_process(target.name, proc, f"{python_path} {filepath}", str(target.parent))

    full_output = ""
    buffer = ""
    last_send = asyncio.get_event_loop().time()

    try:
        while True:
            try:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=5)
            except asyncio.TimeoutError:
                # Send buffer if we have accumulated output
                if buffer and callback:
                    await callback(buffer)
                    buffer = ""
                    last_send = asyncio.get_event_loop().time()
                if asyncio.get_event_loop().time() - last_send > timeout:
                    proc.kill()
                    full_output += "\n(killed: timeout)"
                    break
                continue

            if not line:
                break

            decoded = line.decode("utf-8", errors="replace")
            full_output += decoded
            buffer += decoded

            # Send every 5 seconds or if buffer is large
            now = asyncio.get_event_loop().time()
            if callback and (now - last_send >= 5 or len(buffer) > 1500):
                await callback(buffer)
                buffer = ""
                last_send = now
    except Exception as e:
        full_output += f"\nError: {e}"

    # Send remaining buffer
    if buffer and callback:
        await callback(buffer)

    await proc.wait()
    add_to_history(f"watch {filepath}", proc.returncode or 0, full_output[:200])
    return proc.returncode or 0, full_output


async def tail_file(filepath: str, callback=None, interval: float = 3,
                    max_duration: int = 300) -> str:
    """Monitor a file for new content. Calls callback(new_lines) when file grows.
    Runs for max_duration seconds. Returns when stopped or timeout."""
    target = Path(filepath)
    if not target.exists():
        return f"File not found: {filepath}"

    import time
    start = time.time()
    last_size = target.stat().st_size

    try:
        while time.time() - start < max_duration:
            await asyncio.sleep(interval)
            try:
                current_size = target.stat().st_size
            except OSError:
                continue

            if current_size > last_size:
                with open(target, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(last_size)
                    new_content = f.read(current_size - last_size)
                last_size = current_size
                if new_content.strip() and callback:
                    await callback(new_content[-2000:])
            elif current_size < last_size:
                # File was truncated/rotated
                last_size = current_size
    except asyncio.CancelledError:
        pass

    return f"Stopped tailing {filepath}"
