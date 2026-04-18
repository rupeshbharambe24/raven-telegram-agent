import logging
from pathlib import Path

from config import Config

logger = logging.getLogger(__name__)


def validate_path(filepath: str) -> tuple[bool, Path]:
    """Ensure the path is within allowed directories."""
    target = Path(filepath).resolve()
    for allowed in Config.ALLOWED_PATHS:
        try:
            target.relative_to(allowed.resolve())
            return True, target
        except ValueError:
            continue
    return False, target


def read_file(filepath: str, max_chars: int = 3000) -> str:
    ok, target = validate_path(filepath)
    if not ok:
        return f"Access denied: {filepath} is outside allowed directories."
    if not target.exists():
        return f"File not found: {filepath}"
    if not target.is_file():
        return f"Not a file: {filepath}"
    try:
        content = target.read_text(encoding="utf-8", errors="replace")
        if len(content) > max_chars:
            return content[:max_chars] + f"\n\n... truncated ({len(content)} chars total)"
        return content
    except Exception as e:
        return f"Error reading file: {e}"


def write_file(filepath: str, content: str) -> str:
    ok, target = validate_path(filepath)
    if not ok:
        return f"Access denied: {filepath} is outside allowed directories."
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"Written {len(content)} chars to {filepath}"
    except Exception as e:
        return f"Error writing file: {e}"


def delete_file(filepath: str) -> str:
    ok, target = validate_path(filepath)
    if not ok:
        return f"Access denied: {filepath} is outside allowed directories."
    if not target.exists():
        return f"File not found: {filepath}"
    if not target.is_file():
        return f"Not a file (won't delete directories): {filepath}"
    try:
        target.unlink()
        return f"Deleted: {filepath}"
    except Exception as e:
        return f"Error deleting: {e}"


def list_dir(dirpath: str, max_items: int = 50) -> str:
    ok, target = validate_path(dirpath)
    if not ok:
        return f"Access denied: {dirpath} is outside allowed directories."
    if not target.exists():
        return f"Directory not found: {dirpath}"
    if not target.is_dir():
        return f"Not a directory: {dirpath}"
    try:
        items = sorted(target.iterdir())[:max_items]
        lines = []
        for item in items:
            prefix = "[DIR] " if item.is_dir() else "      "
            size = ""
            if item.is_file():
                sz = item.stat().st_size
                if sz < 1024:
                    size = f"  ({sz}B)"
                elif sz < 1024 * 1024:
                    size = f"  ({sz // 1024}KB)"
                else:
                    size = f"  ({sz // (1024 * 1024)}MB)"
            lines.append(f"{prefix}{item.name}{size}")
        result = "\n".join(lines)
        if len(items) >= max_items:
            result += f"\n... (first {max_items} shown)"
        return result or "(empty directory)"
    except Exception as e:
        return f"Error listing directory: {e}"


def get_file_for_send(filepath: str) -> tuple[str | None, str | None]:
    """Return path if file exists and is sendable, else error message."""
    ok, target = validate_path(filepath)
    if not ok:
        return None, "Access denied"
    if not target.exists():
        return None, "File not found"
    if not target.is_file():
        return None, "Not a file"
    if target.stat().st_size > 50 * 1024 * 1024:
        return None, "File too large (>50MB Telegram limit)"
    return str(target), None


def find_files(query: str, max_results: int = 15) -> list[Path]:
    """Search across ALL allowed paths for files matching query (case-insensitive).
    More thorough than search_files — scans every allowed directory."""
    query_words = [w.lower() for w in query.split() if len(w) > 1]
    if not query_words:
        return []

    matches = []
    seen = set()

    for base in Config.ALLOWED_PATHS:
        base_resolved = base.resolve()
        if not base_resolved.exists() or not base_resolved.is_dir():
            continue
        try:
            for item in base_resolved.rglob("*"):
                if not item.is_file() or str(item) in seen:
                    continue
                seen.add(str(item))
                name_lower = item.stem.lower()
                if all(w in name_lower for w in query_words):
                    matches.append(item)
                    if len(matches) >= max_results:
                        return matches
        except (PermissionError, OSError):
            continue

    # Fallback: partial match
    if not matches:
        for base in Config.ALLOWED_PATHS:
            base_resolved = base.resolve()
            if not base_resolved.exists():
                continue
            try:
                for item in base_resolved.rglob("*"):
                    if not item.is_file() or str(item) in seen:
                        continue
                    seen.add(str(item))
                    name_lower = item.name.lower()
                    if any(w in name_lower for w in query_words if len(w) > 2):
                        matches.append(item)
                        if len(matches) >= max_results:
                            return matches
            except (PermissionError, OSError):
                continue

    return matches


def tree(dirpath: str, max_depth: int = 3, max_items: int = 100) -> str:
    """Show directory tree up to max_depth levels deep."""
    ok, target = validate_path(dirpath)
    if not ok:
        return f"Access denied: {dirpath}"
    if not target.exists() or not target.is_dir():
        return f"Not a directory: {dirpath}"

    lines = [str(target)]
    count = 0

    def _walk(path: Path, prefix: str, depth: int):
        nonlocal count
        if depth > max_depth or count > max_items:
            return
        try:
            entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except (PermissionError, OSError):
            return
        dirs = [e for e in entries if e.is_dir() and not e.name.startswith(".")]
        files = [e for e in entries if e.is_file()]
        items = dirs + files[:20]  # limit files per dir

        for i, entry in enumerate(items):
            count += 1
            if count > max_items:
                lines.append(f"{prefix}... (truncated)")
                return
            connector = "└── " if i == len(items) - 1 else "├── "
            suffix = "/" if entry.is_dir() else ""
            lines.append(f"{prefix}{connector}{entry.name}{suffix}")
            if entry.is_dir():
                extension = "    " if i == len(items) - 1 else "│   "
                _walk(entry, prefix + extension, depth + 1)

    _walk(target, "", 1)
    return "\n".join(lines) if lines else "(empty)"


def recent_files(dirpath: str | None = None, hours: int = 24, max_results: int = 20) -> str:
    """Find files modified in the last N hours."""
    import time
    base = Path(dirpath) if dirpath else Config.WORKSPACE
    ok, target = validate_path(str(base))
    if not ok or not target.exists():
        return f"Invalid path: {base}"

    cutoff = time.time() - (hours * 3600)
    recent = []

    try:
        for item in target.rglob("*"):
            if not item.is_file():
                continue
            if item.name.startswith(".") or "__pycache__" in str(item):
                continue
            try:
                mtime = item.stat().st_mtime
                if mtime > cutoff:
                    recent.append((mtime, item))
            except OSError:
                continue
    except (PermissionError, OSError):
        pass

    if not recent:
        return f"No files modified in the last {hours}h"

    recent.sort(key=lambda x: x[0], reverse=True)
    recent = recent[:max_results]

    lines = [f"Files modified in last {hours}h:\n"]
    for mtime, item in recent:
        import datetime
        dt = datetime.datetime.fromtimestamp(mtime)
        time_str = dt.strftime("%m/%d %H:%M")
        rel = item.relative_to(target) if str(item).startswith(str(target)) else item
        lines.append(f"  {time_str}  {rel}")

    return "\n".join(lines)


def search_files(directory: str, query: str, max_results: int = 10) -> list[Path]:
    """Search for files matching a query (case-insensitive) in a directory tree.
    Returns list of matching file paths."""
    ok, target = validate_path(directory)
    if not ok or not target.exists() or not target.is_dir():
        return []

    query_words = query.lower().split()
    matches = []

    try:
        for item in target.rglob("*"):
            if not item.is_file():
                continue
            name_lower = item.stem.lower()
            # File matches if all query words appear in the filename
            if all(w in name_lower for w in query_words if len(w) > 1):
                matches.append(item)
                if len(matches) >= max_results:
                    break
    except PermissionError:
        pass

    # If strict matching found nothing, try partial matching (any word)
    if not matches:
        try:
            for item in target.rglob("*"):
                if not item.is_file():
                    continue
                name_lower = item.name.lower()
                if any(w in name_lower for w in query_words if len(w) > 2):
                    matches.append(item)
                    if len(matches) >= max_results:
                        break
        except PermissionError:
            pass

    return matches
