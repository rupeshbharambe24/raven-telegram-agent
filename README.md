# RAVEN - Telegram AI Agent

> Control your development machine from anywhere. Send a raven.

A personal AI agent that runs on WSL2, connects to cloud + local LLMs, and gives you full remote control of your dev environment through Telegram — complete with a permission system so it can't do anything destructive without your explicit approval.

![Python](https://img.shields.io/badge/Python-3.10+-blue)
![Telegram](https://img.shields.io/badge/Telegram-Bot%20API-blue)
![LLMs](https://img.shields.io/badge/LLMs-Groq%20%7C%20Gemini%20%7C%20Ollama-purple)
![License](https://img.shields.io/badge/License-MIT-green)

## What It Does

**Send a message. Your machine does the work.**

- Ask LLM questions from your phone (Groq/Gemini/Ollama cascade)
- Run Python scripts and see output in real-time
- Execute Jupyter notebooks cell-by-cell with live output
- Start dev servers, auto-detect the port, open browser, take screenshots
- Find files, read code, manage git — all from Telegram
- Get GPU status, set reminders, monitor running processes
- **Every write/delete operation requires your tap-to-approve**

### Demo Flow

```
You:   "go to D drive, open my react project, run npm dev and screenshot it"

RAVEN: [Plans the steps, asks for approval]
You:   [Tap Approve]

RAVEN: [Starts dev server via PowerShell]
       [Auto-detects port 5173]
       [Opens browser, waits for load]
       [Sends screenshot to your phone]
```

### Startup Dashboard

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
       R A V E N  v2.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Time:    2026-04-18 18:30
  Groq:    ON
  Gemini:  ON
  Ollama:  ON
  Disk:    45GB free
  GPU:     NVIDIA RTX 3060 (1024/12288 MB)

  All systems online.
  Send /help for commands.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

## Architecture

```
Your Phone (Telegram)
        |
   Telegram Bot API
        |
     RAVEN Agent (WSL2)
    /       |        \
Groq     Gemini    Ollama
(70B)    (Flash)   (7B fallback)
    \       |        /
     Windows Machine
     (files, GPU, browser, dev servers)
```

**LLM Cascade**: Tries Groq first (fast, 70B model), falls back to Gemini Flash, then local Ollama. Automatic failover on rate limits or errors.

## Commands (36 total)

### LLM
| Command | Description | Approval |
|---------|-------------|----------|
| `/ask <prompt>` | Ask the LLM anything | Auto |
| `/code <prompt>` | Code-focused query | Auto |

### Files
| Command | Description | Approval |
|---------|-------------|----------|
| `/read <path>` | Read file contents | Auto |
| `/ls <path>` | List directory | Auto |
| `/send <path>` | Send file to you | Auto |
| `/find <name>` | Search for files across drives | Auto |
| `/tree <path>` | Directory tree view | Auto |
| `/recent` | Recently modified files | Auto |
| `/write <path>` | Write/create file | **Approval** |
| `/delete <path>` | Delete file | **Approval** |

### Process Management
| Command | Description | Approval |
|---------|-------------|----------|
| `/run <script.py>` | Run Python script | **Approval** |
| `/watch <script>` | Run with live streaming output | **Approval** |
| `/cmd <command>` | Run any shell command | **Approval** |
| `/tail <logfile>` | Monitor log file live | Auto |
| `/procs` | Show background processes | Auto |
| `/kill <name>` | Kill a background process | **Approval** |
| `/history` | Command history | Auto |

### Jupyter Notebooks
| Command | Description | Approval |
|---------|-------------|----------|
| `/nb open <path>` | Load a notebook | Auto |
| `/nb run` | Run all cells sequentially | **Approval** |
| `/nb run 5` | Run specific cell | **Approval** |
| `/nb run 5+` | Run from cell 5 onward | **Approval** |
| `/nb cell 5` | Show cell source code | Auto |
| `/nb edit 5` | Edit a cell | **Approval** |
| `/nb out 5` | Show cell output | Auto |
| `/nb env <path>` | Set Python environment | Auto |
| `/nb status` | Kernel status | Auto |
| `/nb stop` | Shutdown kernel | Auto |

### Git (local only, never pushes)
| Command | Description | Approval |
|---------|-------------|----------|
| `/commit <msg>` | Commit changes | **Approval** |
| `/diff` | Show uncommitted changes | Auto |
| `/gitlog` | Recent commits | Auto |
| `/undo` | Soft reset last commit | **Approval** |

### System
| Command | Description | Approval |
|---------|-------------|----------|
| `/do <task>` | Multi-step task (natural language) | **Approval** |
| `/screenshot` | Capture screen | Auto |
| `/status` | System + LLM provider status | Auto |
| `/gpu` | NVIDIA GPU usage + VRAM + temp | Auto |
| `/models` | List Ollama models | Auto |
| `/logs` | Agent logs | Auto |
| `/remind <min> <msg>` | Set a reminder | Auto |

### Utility
| Command | Description |
|---------|-------------|
| `/open <path>` | Open in Windows Explorer |
| `/clip <text>` | Copy to Windows clipboard |
| `/bookmark <name> <path>` | Save a path shortcut |
| `/go <name>` | Navigate to bookmark |
| `/persona` | Switch AI personality |
| `/stop` | Shutdown agent cleanly |

### Personality System

RAVEN comes with switchable AI personalities:

```
/persona              — show current + all presets
/persona raven        — sharp, direct, darkly witty (default)
/persona formal       — professional, no personality
/persona mentor       — patient, teaches as it goes
/persona brutal       — extremely blunt, no sugar coating
/persona pirate       — nautical terms, fun but helpful
/persona zen          — calm, philosophical, minimalist
/persona set <prompt> — fully custom personality
/persona reset        — back to default
```

Persists across restarts. Create your own with `/persona set You are a sarcastic cat who judges every command`.

### Natural Language

You don't always need commands. Just type naturally:

- *"send me the training notebook from D drive"*
- *"find the config file"*
- *"run test.py"*
- *"take a screenshot"*
- *"what's running in the background?"*

## Setup

### Prerequisites

- Windows 10/11 with WSL2 (Ubuntu)
- Python 3.10+ in WSL
- [Ollama](https://ollama.com) installed on Windows
- A Telegram bot token (from [@BotFather](https://t.me/BotFather))
- Your Telegram chat ID (from [@userinfobot](https://t.me/userinfobot))

### Quick Start

```bash
# Clone
git clone https://github.com/rupeshbharambe24/raven-telegram-agent.git
cd raven-telegram-agent

# Configure
cp .env.example .env
# Edit .env with your tokens and API keys

# Make Ollama accessible from WSL (run in Windows PowerShell):
[System.Environment]::SetEnvironmentVariable("OLLAMA_HOST", "0.0.0.0", "User")
# Then restart Ollama

# Setup (creates venv on Linux fs, installs deps, tests connections)
bash setup.sh

# Run
source ~/.venvs/ai-agent/bin/activate
python3 main.py
```

### Get Free API Keys

| Provider | Model | Free Tier | Link |
|----------|-------|-----------|------|
| **Groq** | Llama 3.3 70B | 14K req/day | [console.groq.com](https://console.groq.com) |
| **Gemini** | Flash | 1500 req/day | [aistudio.google.com](https://aistudio.google.com/apikey) |
| **Ollama** | qwen, deepseek, etc. | Unlimited (local) | [ollama.com](https://ollama.com) |

### Run as Service

```bash
sudo cp ai-agent.service /etc/systemd/system/
sudo systemctl enable ai-agent
sudo systemctl start ai-agent
```

## Permission System

RAVEN uses a **read-auto, write-approve** model:

- **Read operations** (read, ls, find, screenshot, status) execute immediately
- **Write operations** (write, delete, run, commit) send an inline keyboard:

```
PERMISSION REQUIRED
Action: Run script: train.py
[Approve] [Deny]
```

Tap to approve or deny. 5-minute timeout auto-denies.

## Project Structure

```
raven-telegram-agent/
├── main.py                 # Entry point
├── config.py               # Configuration from .env
├── .env.example            # Template for secrets
├── requirements.txt        # Python dependencies
├── setup.sh                # One-command setup
├── core/
│   ├── bot.py              # Telegram handlers (36 commands)
│   ├── llm_cascade.py      # Groq -> Gemini -> Ollama
│   ├── guard.py            # Permission system
│   ├── brain.py            # Natural language intent classifier
│   ├── monitor.py          # Script execution + error recovery
│   └── notebook.py         # Jupyter kernel manager
└── tools/
    ├── file_ops.py         # File operations + search
    ├── process_ops.py      # Process management + streaming
    ├── git_ops.py          # Git operations (local only)
    ├── screenshot.py       # Windows screen capture
    └── system_info.py      # System status
```

## Contributing

Contributions welcome. Some ideas:

- **Docker support** — containerize for easier deployment
- **Voice messages** — TTS/STT via Telegram voice
- **More LLM providers** — Claude, local llama.cpp
- **Tests** — no test suite yet
- **Linux native** — remove WSL2 dependency
- **Web dashboard** — optional web UI alongside Telegram
- **Multi-user** — support multiple authorized users

### How to Contribute

1. Fork the repo
2. Create a feature branch (`git checkout -b feature/voice-messages`)
3. Make your changes
4. Test with your own bot
5. Submit a PR

## License

MIT License — see [LICENSE](LICENSE) for details.

---

Built by [@rupeshbharambe24](https://github.com/rupeshbharambe24)
