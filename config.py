import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")


class Config:
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
    TELEGRAM_CHAT_ID = int(os.getenv("TELEGRAM_CHAT_ID", "0"))

    # Auto-detect Windows host IP for WSL -> Windows Ollama connectivity
    OLLAMA_URL = os.getenv("OLLAMA_URL", "").strip()
    if not OLLAMA_URL:
        try:
            with open("/etc/resolv.conf") as f:
                for line in f:
                    if line.strip().startswith("nameserver"):
                        host_ip = line.split()[-1]
                        OLLAMA_URL = f"http://{host_ip}:11434"
                        break
        except FileNotFoundError:
            pass
        if not OLLAMA_URL:
            OLLAMA_URL = "http://localhost:11434"

    DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "qwen2.5:7b")
    CODE_MODEL = os.getenv("CODE_MODEL", "deepseek-coder:6.7b")

    # Cloud LLM cascade (Groq -> Gemini -> local Ollama)
    GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
    GROQ_MODEL = "llama-3.3-70b-versatile"
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
    GEMINI_MODEL = "gemini-2.0-flash"

    WORKSPACE = Path(os.getenv("WORKSPACE", str(Path(__file__).parent)))
    LOG_FILE = Path(os.getenv("LOG_FILE", "logs/agent.log"))
    PERMISSION_TIMEOUT = int(os.getenv("PERMISSION_TIMEOUT", "300"))

    # Security: restrict file operations to these base directories
    # Configurable via ALLOWED_PATHS env var (comma-separated), or defaults to common locations
    _extra_paths = os.getenv("ALLOWED_PATHS", "")
    ALLOWED_PATHS = [WORKSPACE]
    if _extra_paths:
        ALLOWED_PATHS.extend(Path(p.strip()) for p in _extra_paths.split(",") if p.strip())
    else:
        # Default: user home + all mounted drives
        home = Path.home()
        if home.exists():
            ALLOWED_PATHS.append(home)
        for drive in Path("/mnt").iterdir() if Path("/mnt").exists() else []:
            if drive.is_dir() and len(drive.name) == 1:
                ALLOWED_PATHS.append(drive)
