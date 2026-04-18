# J.A.R.V.I.S. - Telegram AI Agent

> Control your development machine from anywhere using Telegram + local LLMs with cloud fallback.

A personal AI agent that runs on WSL2, connects to your local Ollama models, and gives you full remote control of your dev environment through Telegram - complete with a permission system so it can't do anything destructive without your explicit approval.

![Python](https://img.shields.io/badge/Python-3.10+-blue)
![Telegram](https://img.shields.io/badge/Telegram-Bot%20API-blue)
![License](https://img.shields.io/badge/License-MIT-green)

## What It Does

**Send a Telegram message. Your machine does the work.**

- Ask your LLM questions from your phone (Groq/Gemini/Ollama cascade)
- Run Python scripts and see output in real-time
- Execute Jupyter notebooks cell-by-cell with live output
- Start dev servers, auto-detect the port, open browser, take screenshots
- Find files, read code, manage git - all from Telegram
- Get GPU status, set reminders, monitor running processes
- **Every write/delete operation requires your tap-to-approve**

### Demo Flow
```
You: "go to D drive, open my react project, run npm dev and screenshot it"

JARVIS: [Plans the steps, asks for approval]
You: [Tap Approve]

JARVIS: [Starts dev server via PowerShell]
        [Auto-detects port 5173]
        [Opens browser]
        [Waits for page load]
        [Sends screenshot to your phone]
```

## Architecture

```
Your Phone (Telegram)
        |
   Telegram Bot API
        |
  J.A.R.V.I.S. Agent (WSL2)
   /        |          \
Groq     Gemini     Ollama (local)
(70B)    (Flash)    (7B fallback)
   \        |          /
    Windows Machine
    (files, browser, GPU, dev servers)
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

### Natural Language
You don't always need commands. Just type:
- "send me the training notebook from D drive"
- "find the config file"
- "run test.py"
- "take a screenshot"
- "what's running in the background?"

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
git clone https://github.com/rupeshbharambe24/jarvis-telegram-agent.git
cd jarvis-telegram-agent

# Configure
cp .env.example .env
# Edit .env with your tokens and API keys

# Make Ollama accessible from WSL (Windows PowerShell):
# [System.Environment]::SetEnvironmentVariable("OLLAMA_HOST", "0.0.0.0", "User")
# Then restart Ollama

# Setup (creates venv, installs deps, tests connections)
bash setup.sh

# Run
source ~/.venvs/ai-agent/bin/activate
python3 main.py
```

### Get Free API Keys
- **Groq** (Llama 3.3 70B): https://console.groq.com - 14K requests/day free
- **Gemini Flash**: https://aistudio.google.com/apikey - 1500 requests/day free

### Run as Service (auto-start)
```bash
sudo cp ai-agent.service /etc/systemd/system/
sudo systemctl enable ai-agent
sudo systemctl start ai-agent
```

## Permission System

The agent uses a **read-auto, write-approve** model:

- **Read operations** (read, ls, find, screenshot, status) execute immediately
- **Write operations** (write, delete, run, commit) send an inline keyboard to Telegram:
  ```
  PERMISSION REQUIRED
  Action: Run script: train.py
  [Approve] [Deny]
  ```
- You tap Approve or Deny on your phone
- 5-minute timeout auto-denies if no response

## Project Structure

```
jarvis-telegram-agent/
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
    ├── screenshot.py       # Windows screen capture via PowerShell
    └── system_info.py      # System status
```

## Contributing

Contributions are welcome! Some areas that could use help:

- **Docker support** - containerize the agent for easier deployment
- **Voice messages** - send/receive voice via Telegram + TTS/STT
- **Multi-user** - support multiple authorized Telegram users
- **Web dashboard** - optional web UI alongside Telegram
- **More LLM providers** - Claude, local llama.cpp, etc.
- **Tests** - the project currently has no test suite
- **Linux native support** - remove WSL2 dependency

### How to Contribute
1. Fork the repo
2. Create a feature branch (`git checkout -b feature/voice-messages`)
3. Make your changes
4. Test manually with your own bot
5. Submit a PR with a clear description

## License

MIT License - see [LICENSE](LICENSE) for details.

---

Built by [@rupeshbharambe24](https://github.com/rupeshbharambe24) with help from Claude Code.
