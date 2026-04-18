import asyncio
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

TIMEOUT = 15
MAX_OUTPUT = 3000


async def _run_git(repo_path: str, *args: str) -> tuple[int, str, str]:
    """Run a git command in the given repo directory.
    Returns (returncode, stdout, stderr)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=repo_path,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=TIMEOUT)
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
        return -1, "", f"Git command timed out after {TIMEOUT}s"
    except Exception as e:
        return -1, "", str(e)


def _truncate(text: str, limit: int = MAX_OUTPUT) -> str:
    """Truncate text to fit Telegram message limits."""
    if len(text) > limit:
        return text[:limit] + f"\n\n... truncated ({len(text)} chars total)"
    return text


async def git_status(repo_path: str) -> str:
    """Run git status in the repo. Return formatted output."""
    code, stdout, stderr = await _run_git(repo_path, "status", "--short", "--branch")
    if code != 0:
        return f"git status failed: {stderr.strip()}"
    if not stdout.strip():
        return "Working tree clean, nothing to commit."
    return _truncate(stdout.strip())


async def git_diff(repo_path: str, staged: bool = False) -> str:
    """Show git diff (or --staged). Truncate to 3000 chars."""
    args = ["diff", "--stat"]
    if staged:
        args.append("--staged")
    code, stdout, stderr = await _run_git(repo_path, *args)
    if code != 0:
        return f"git diff failed: {stderr.strip()}"
    if not stdout.strip():
        label = "staged" if staged else "unstaged"
        return f"No {label} changes."

    # Also get the actual diff (limited) for context
    full_args = ["diff"]
    if staged:
        full_args.append("--staged")
    code2, diff_out, _ = await _run_git(repo_path, *full_args)

    result = stdout.strip()
    if code2 == 0 and diff_out.strip():
        result += "\n\n" + diff_out.strip()
    return _truncate(result)


async def git_log(repo_path: str, count: int = 10) -> str:
    """Show last N commits with short hash, date, message."""
    fmt = "--pretty=format:%h  %ad  %s"
    code, stdout, stderr = await _run_git(
        repo_path, "log", f"-{count}", fmt, "--date=short",
    )
    if code != 0:
        return f"git log failed: {stderr.strip()}"
    if not stdout.strip():
        return "No commits yet."
    return _truncate(stdout.strip())


async def git_commit(repo_path: str, message: str, files: list[str] | None = None) -> str:
    """Stage files (or all if None) and commit with message.
    NEVER pushes. Returns commit hash or error."""
    # Stage files
    if files:
        for f in files:
            code, _, stderr = await _run_git(repo_path, "add", f)
            if code != 0:
                return f"Failed to stage {f}: {stderr.strip()}"
    else:
        code, _, stderr = await _run_git(repo_path, "add", "-A")
        if code != 0:
            return f"Failed to stage files: {stderr.strip()}"

    # Check if there is anything to commit
    code, stdout, _ = await _run_git(repo_path, "diff", "--staged", "--quiet")
    if code == 0:
        return "Nothing to commit (no staged changes)."

    # Commit
    code, stdout, stderr = await _run_git(repo_path, "commit", "-m", message)
    if code != 0:
        return f"Commit failed: {stderr.strip()}"

    # Get the commit hash
    code, hash_out, _ = await _run_git(repo_path, "rev-parse", "--short", "HEAD")
    commit_hash = hash_out.strip() if code == 0 else "unknown"

    return f"Committed: {commit_hash} - {message}"


async def git_undo_last(repo_path: str) -> str:
    """Soft reset last commit (git reset --soft HEAD~1). Keeps changes staged."""
    # First check there is a commit to undo
    code, stdout, stderr = await _run_git(repo_path, "log", "-1", "--oneline")
    if code != 0:
        return f"Cannot undo: {stderr.strip()}"

    undone = stdout.strip()
    code, _, stderr = await _run_git(repo_path, "reset", "--soft", "HEAD~1")
    if code != 0:
        return f"Reset failed: {stderr.strip()}"

    return f"Undone commit: {undone}\nChanges are still staged."


async def is_git_repo(path: str) -> bool:
    """Check if path is inside a git repo."""
    code, _, _ = await _run_git(path, "rev-parse", "--is-inside-work-tree")
    return code == 0


async def git_auto_commit(repo_path: str, filepath: str, description: str) -> str:
    """Stage a single file and commit with auto-generated message.
    Message format: 'agent: {description}'
    Used after agent makes changes with user approval."""
    message = f"agent: {description}"
    return await git_commit(repo_path, message, files=[filepath])
