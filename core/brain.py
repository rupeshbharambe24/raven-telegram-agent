import re
import logging

logger = logging.getLogger(__name__)

# Action verbs that indicate discrete steps
_ACTION_VERBS = r"\b(go|navigate|cd|activate|source|run|execute|start|open|launch|send|take|screenshot|install|build|deploy|stop|kill|restart)\b"

# (pattern, intent) — checked in order, first match wins
_PATTERNS = [
    # Notebook
    (r"\b(notebook|\.ipynb|jupyter|nb)\b.*\b(open|run|cell|edit|stop|start)\b", "notebook"),
    (r"\b(open|run|execute)\b.*\b(notebook|\.ipynb)\b", "notebook"),
    (r"\b(train|training)\b.*\b(model|notebook|cell)\b", "notebook"),
    # Git
    (r"\b(commit|git commit)\b", "git_commit"),
    (r"\b(git diff|show changes|what changed)\b", "git_diff"),
    (r"\b(git log|commit history|recent commits)\b", "git_log"),
    # Scripts
    (r"\b(run|execute|start)\b.*\.py\b", "run_script"),
    (r"\b(watch|monitor)\b.*\.py\b", "watch_script"),
    # Screenshot
    (r"\b(screenshot|screenshort|screen\s?cap|capture\s?screen|screen\s?shot|snap\s?screen)\b", "screenshot"),
    # File operations
    (r"\b(find|search|locate|where is)\b.*\b(file|\.py|\.txt|\.json|\.ipynb|\.ppt|\.pdf|\.doc)\b", "find_file"),
    (r"\b(find|search|locate)\b", "find_file"),
    (r"\b(send|give|get|fetch|share|download)\b.*\b(me|file|ppt|pptx|pdf|doc|docx|xls|xlsx|zip|rar|img|image|photo|csv|txt|py)\b", "send_file"),
    (r"\b(send me)\b", "send_file"),
    (r"\b(read|show|cat|view)\b.*\b(file|\.py|\.txt|\.json|\.log|\.md|\.csv|\.cfg|\.ya?ml)\b", "read_file"),
    (r"\b(write|save|create|modify)\b.*\b(file|to)\b", "write_file"),
    (r"\b(delete|remove|rm)\b.*\b(file|\.py|\.txt|\.json)\b", "delete_file"),
    (r"\b(list|ls|dir)\b.*\b(folder|directory|files|dir|contents)\b", "list_dir"),
    (r"\b(go\s+to|cd|navigate)\b.*\b(and|then)\b.*\b(ls|list|show|see)\b", "list_dir"),
    (r"\b(ls|list)\b.*\b([a-zA-Z]\s*drive)\b", "list_dir"),
    (r"\b([a-zA-Z]\s*drive)\b.*\b(ls|list|show)\b", "list_dir"),
    (r"\b(tree|structure|directory tree)\b", "tree"),
    (r"\b(recent|recently|modified|changed)\b.*\b(files?|today|yesterday)\b", "recent_files"),
    # System
    (r"\b(status|health|alive)\b", "status"),
    (r"\b(models?|which models?)\b", "models"),
    (r"\b(logs?|show logs?)\b", "logs"),
    (r"\b(process|procs|running|background)\b.*\b(list|show|what)\b", "procs"),
    (r"\b(kill|stop|terminate)\b.*\b(process|proc|server|script)\b", "kill_proc"),
    (r"\b(fix|solve|debug|repair|diagnose)\b", "fix_error"),
]

# Map natural language drive references to WSL mount paths
_DRIVE_PATTERNS = [
    (r"\b([a-zA-Z])\s*drive\b", lambda m: f"/mnt/{m.group(1).lower()}"),
    (r"\b([a-zA-Z]):\s*[/\\]", lambda m: f"/mnt/{m.group(1).lower()}/"),
    (r"\b([a-zA-Z]):\\", lambda m: f"/mnt/{m.group(1).lower()}/"),
]


def classify(text: str) -> tuple[str, str]:
    """Classify free text into (intent, extracted_arg). Falls back to ask_llm."""
    lower = text.lower().strip()

    # Detect multi-step commands first (3+ action verbs = complex task)
    action_count = len(re.findall(_ACTION_VERBS, lower))
    if action_count >= 3:
        return "multi_step", text

    for pattern, intent in _PATTERNS:
        if re.search(pattern, lower):
            if intent == "send_file":
                return intent, _build_search_info(text)
            return intent, _extract_path(text)
    return "ask_llm", text


def _build_search_info(text: str) -> str:
    """Extract directory + search query from natural language file requests."""
    directory = _extract_directory(text)
    query = _extract_filename_query(text)
    return f"{directory}::{query}"


def _extract_directory(text: str) -> str:
    """Extract a directory path from natural language."""
    lower = text.lower()

    for pat in (r"(/mnt/[\w./-]+)", r"([A-Za-z]:[\\w./\\-]+)"):
        m = re.search(pat, text)
        if m:
            return m.group(1)

    drive_match = re.search(r"\b([a-zA-Z])\s*drive\b", lower)
    drive_base = f"/mnt/{drive_match.group(1).lower()}" if drive_match else ""

    folder_parts = []
    path_match = re.search(r"[\w]+[/\\][\w/\\]+", text)
    if path_match:
        folder_parts = [path_match.group(0).replace("\\", "/")]
    else:
        folder_match = re.search(
            r"(?:from|in|at|under|inside)\s+(?:the\s+)?(.+?)(?:\s+folder|\s+directory|\s+dir)?$",
            lower,
        )
        if folder_match:
            raw = folder_match.group(1).strip()
            # Remove common words AND drive references to avoid "r/drive" duplication
            raw = re.sub(
                r"\b(send|me|get|give|fetch|share|file|ppt|pptx|pdf|doc|the|a|an|"
                r"drive|[a-z]\s+drive)\b",
                "", raw
            )
            parts = [p.strip() for p in raw.split() if p.strip() and len(p.strip()) > 1]
            if parts:
                folder_parts = ["/".join(parts)]

    if drive_base and folder_parts:
        # Don't append folder if it's just the drive letter repeated
        folder = folder_parts[0].strip("/")
        if folder and folder != drive_base.split("/")[-1]:
            return f"{drive_base}/{folder}"
        return drive_base
    elif drive_base:
        return drive_base
    elif folder_parts:
        return folder_parts[0]
    return _Config_WORKSPACE


def _extract_filename_query(text: str) -> str:
    """Extract what the user is looking for (filename/description)."""
    lower = text.lower()
    cleaned = re.sub(
        r"\b(send|me|give|get|fetch|share|download|from|the|a|an|in|at|on|my|"
        r"[a-z]\s*drive|drive|folder|directory|dir|file|pdf|ppt|doc)\b",
        " ", lower
    )
    cleaned = re.sub(r"[\w]+[/\\][\w/\\]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


import os as _os
_Config_WORKSPACE = _os.getenv("WORKSPACE", ".")


def _extract_path(text: str) -> str:
    """Try to pull a file/dir path out of the message."""
    for pat in (r"(/mnt/[\w./-]+)", r"(/[\w./-]+)", r"([A-Za-z]:[\\w./\\-]+)"):
        m = re.search(pat, text)
        if m:
            return m.group(1)

    lower = text.lower()
    drive_match = re.search(r"\b([a-zA-Z])\s*drive\b", lower)
    if drive_match:
        base = f"/mnt/{drive_match.group(1).lower()}"
        path_match = re.search(r"[\w]+[/\\][\w/\\]+", text)
        if path_match:
            return f"{base}/{path_match.group(0).replace(chr(92), '/')}"
        return base

    for token in text.split():
        if "." in token and not token.startswith("http"):
            return token
    return text
