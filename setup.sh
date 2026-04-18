#!/usr/bin/env bash
set -e

VENV_DIR="$HOME/.venvs/ai-agent"
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "==============================="
echo "  AI Agent Setup"
echo "==============================="
echo ""

# Check Python 3
if ! command -v python3 &>/dev/null; then
    echo "[ERROR] Python 3 not found."
    echo "  Install with: sudo apt update && sudo apt install python3 python3-pip python3-venv"
    exit 1
fi
echo "[OK] Python 3 found: $(python3 --version)"

# Create venv on Linux filesystem (NTFS /mnt/c breaks venvs)
if [ ! -f "$VENV_DIR/bin/activate" ]; then
    echo "[SETUP] Creating virtual environment at $VENV_DIR ..."
    mkdir -p "$(dirname "$VENV_DIR")"
    rm -rf "$VENV_DIR"
    python3 -m venv "$VENV_DIR"
fi
echo "[OK] Virtual environment ready at $VENV_DIR"

# Activate and install dependencies
echo "[SETUP] Installing dependencies..."
source "$VENV_DIR/bin/activate"
pip install --upgrade pip -q
pip install -r "$PROJECT_DIR/requirements.txt" -q
echo "[OK] Dependencies installed"

# Create logs directory
mkdir -p "$PROJECT_DIR/logs"

# Check .env
if [ ! -f "$PROJECT_DIR/.env" ]; then
    echo "[ERROR] .env file not found! Create it with your Telegram credentials."
    exit 1
fi
echo "[OK] .env file found"

# Test cloud LLM keys
echo ""
echo "[TEST] Checking LLM API keys..."
GROQ_KEY=$(python3 -c "import sys; sys.path.insert(0,'$PROJECT_DIR'); from config import Config; print(Config.GROQ_API_KEY[:8] + '...' if Config.GROQ_API_KEY else 'NOT SET')")
GEMINI_KEY=$(python3 -c "import sys; sys.path.insert(0,'$PROJECT_DIR'); from config import Config; print(Config.GEMINI_API_KEY[:8] + '...' if Config.GEMINI_API_KEY else 'NOT SET')")
echo "  Groq API key:   $GROQ_KEY"
echo "  Gemini API key:  $GEMINI_KEY"
if [ "$GROQ_KEY" = "NOT SET" ] || [ "$GEMINI_KEY" = "NOT SET" ]; then
    echo "  [WARN] Set GROQ_API_KEY and GEMINI_API_KEY in .env for best performance"
fi

# Check Jupyter kernel
echo ""
echo "[TEST] Checking Jupyter..."
if python3 -c "import jupyter_client" 2>/dev/null; then
    echo "[OK] jupyter_client available"
else
    echo "[WARN] jupyter_client not found - /nb commands won't work until setup reruns"
fi

# Test Ollama connectivity
echo ""
echo "[TEST] Checking Ollama connection..."
OLLAMA_URL=$(python3 -c "import sys; sys.path.insert(0,'$PROJECT_DIR'); from config import Config; print(Config.OLLAMA_URL)")
echo "  Detected Ollama URL: $OLLAMA_URL"

if curl -s --max-time 5 "$OLLAMA_URL" > /dev/null 2>&1; then
    echo "[OK] Ollama reachable at $OLLAMA_URL"
    echo ""
    echo "  Available models:"
    curl -s "$OLLAMA_URL/api/tags" 2>/dev/null | python3 -c "
import sys, json
data = json.load(sys.stdin)
for m in data.get('models', []):
    name = m.get('name', '?')
    size = m.get('size', 0) / (1024**3)
    print(f'    - {name} ({size:.1f}GB)')
" 2>/dev/null || echo "    (could not list models)"
else
    echo "[WARN] Cannot reach Ollama at $OLLAMA_URL"
    echo ""
    echo "  Troubleshooting:"
    echo "  1. Make sure Ollama is running on Windows"
    echo "  2. On Windows, set environment variable: OLLAMA_HOST=0.0.0.0"
    echo "     (System Settings > Environment Variables > User variables)"
    echo "  3. Restart Ollama after setting OLLAMA_HOST"
    echo "  4. Or set OLLAMA_URL manually in .env"
fi

# Clean up old files
if [ -f "$PROJECT_DIR/tools/actions.py" ]; then
    echo ""
    echo "[CLEANUP] Removing old tools/actions.py (replaced by new modules)"
    rm -f "$PROJECT_DIR/tools/actions.py"
    rm -rf "$PROJECT_DIR/tools/__pycache__"
    rm -rf "$PROJECT_DIR/core/__pycache__"
fi

# Remove broken venv on NTFS if it exists
if [ -d "$PROJECT_DIR/venv" ]; then
    echo "[CLEANUP] Removing broken venv from project directory"
    rm -rf "$PROJECT_DIR/venv"
fi

# Generate systemd service file from template
if [ -f "$PROJECT_DIR/ai-agent.service.template" ]; then
    sed -e "s|__WSL_USER__|$(whoami)|g" \
        -e "s|__PROJECT_DIR__|$PROJECT_DIR|g" \
        -e "s|__VENV_DIR__|$VENV_DIR|g" \
        "$PROJECT_DIR/ai-agent.service.template" > "$PROJECT_DIR/ai-agent.service"
    echo "[OK] Systemd service file generated"
fi

echo ""
echo "==============================="
echo "  Setup complete!"
echo "==============================="
echo ""
echo "To run the agent:"
echo "  source $VENV_DIR/bin/activate"
echo "  cd $PROJECT_DIR"
echo "  python3 main.py"
echo ""
echo "Or use the systemd service:"
echo "  sudo cp $PROJECT_DIR/ai-agent.service /etc/systemd/system/"
echo "  sudo systemctl enable ai-agent"
echo "  sudo systemctl start ai-agent"
echo ""
