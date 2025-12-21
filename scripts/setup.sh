#!/bin/bash
set -e

echo "=== Render Voice Agent Setup ==="

# Check Python version
python_version=$(python3 --version 2>&1 | cut -d' ' -f2 | cut -d'.' -f1,2)
required_version="3.11"

if python3 -c "import sys; exit(0 if sys.version_info >= (3, 11) else 1)"; then
    echo "✓ Python version: $python_version"
else
    echo "Error: Python 3.11+ required (found $python_version)"
    exit 1
fi

# Check Node.js
if ! command -v node &> /dev/null; then
    echo "Error: Node.js is required for Claude Code CLI"
    exit 1
fi
echo "✓ Node.js installed"

# Install Claude Code CLI if not present
if ! command -v claude &> /dev/null; then
    echo "Installing Claude Code CLI..."
    npm install -g @anthropic-ai/claude-code
fi
echo "✓ Claude Code CLI installed"

# Create virtual environment
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
fi
source .venv/bin/activate
echo "✓ Virtual environment activated"

# Install dependencies
echo "Installing Python dependencies..."
pip install -e ".[dev]"
echo "✓ Dependencies installed"

# Create .env if not exists
if [ ! -f ".env" ]; then
    echo "Creating .env from template..."
    cp .env.example .env
    echo "⚠ Please edit .env with your API keys"
fi

# Create directories
mkdir -p target-repo logs
touch target-repo/.gitkeep

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Next steps:"
echo "1. Edit .env with your API keys"
echo "2. Run: ./scripts/clone_target_repo.sh"
echo "3. Run: python -m src.main"
echo ""
