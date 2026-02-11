#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# Autonomous Repo Deployer — Setup & Launch
# =============================================================================
# This script checks prerequisites, installs dependencies, and runs the
# orchestrator. Run it once and walk away.
#
# Usage:
#   chmod +x run.sh
#   ./run.sh              # Full send
#   ./run.sh --dry-run    # Test without deploying
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${CYAN}"
echo "╔═══════════════════════════════════════════════════╗"
echo "║     AUTONOMOUS REPO DEPLOYER — FULL SEND MODE    ║"
echo "╚═══════════════════════════════════════════════════╝"
echo -e "${NC}"

# Handle flags
if [[ "${1:-}" == "--dry-run" ]]; then
    export DRY_RUN=true
    echo -e "${YELLOW}🧪 DRY RUN MODE — no repos will be modified or deployed${NC}"
fi

# ---------------------------------------------------------------------------
# Load .env
# ---------------------------------------------------------------------------
if [[ -f .env ]]; then
    echo -e "${GREEN}✓${NC} Loading .env"
    set -a
    source .env
    set +a
else
    echo -e "${RED}✗ No .env file found.${NC}"
    echo "  Copy .env.template to .env and fill in your tokens:"
    echo "  cp .env.template .env"
    exit 1
fi

# ---------------------------------------------------------------------------
# Prerequisite checks
# ---------------------------------------------------------------------------
ERRORS=0

echo ""
echo "Checking prerequisites..."

# Python
if command -v python3 &>/dev/null; then
    PY_VERSION=$(python3 --version 2>&1)
    echo -e "  ${GREEN}✓${NC} $PY_VERSION"
else
    echo -e "  ${RED}✗ Python 3 not found${NC}"
    ERRORS=$((ERRORS + 1))
fi

# Git
if command -v git &>/dev/null; then
    echo -e "  ${GREEN}✓${NC} git $(git --version | cut -d' ' -f3)"
else
    echo -e "  ${RED}✗ git not found${NC}"
    ERRORS=$((ERRORS + 1))
fi

# Claude Code CLI
if command -v claude &>/dev/null; then
    echo -e "  ${GREEN}✓${NC} Claude Code CLI found"
else
    echo -e "  ${RED}✗ Claude Code CLI not found${NC}"
    echo "     Install: npm install -g @anthropic-ai/claude-code"
    echo "     Then:    claude auth login"
    ERRORS=$((ERRORS + 1))
fi

# Node (needed for Claude Code)
if command -v node &>/dev/null; then
    echo -e "  ${GREEN}✓${NC} Node.js $(node --version)"
else
    echo -e "  ${RED}✗ Node.js not found (needed for Claude Code)${NC}"
    ERRORS=$((ERRORS + 1))
fi

# ffmpeg (needed for GIF capture)
if command -v ffmpeg &>/dev/null; then
    echo -e "  ${GREEN}✓${NC} ffmpeg found"
else
    echo -e "  ${YELLOW}⚠${NC} ffmpeg not found — GIF screen captures will be skipped"
    echo "     Install: brew install ffmpeg / sudo apt install ffmpeg"
fi

# Environment variables
if [[ -z "${GITHUB_TOKEN:-}" ]]; then
    echo -e "  ${RED}✗ GITHUB_TOKEN not set${NC}"
    ERRORS=$((ERRORS + 1))
else
    echo -e "  ${GREEN}✓${NC} GITHUB_TOKEN set"
fi

if [[ -z "${RENDER_API_KEY:-}" ]]; then
    echo -e "  ${YELLOW}⚠${NC} RENDER_API_KEY not set — deployments will be skipped"
else
    echo -e "  ${GREEN}✓${NC} RENDER_API_KEY set"
fi

if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
    echo -e "  ${YELLOW}⚠${NC} ANTHROPIC_API_KEY not set — Claude Code may not work"
else
    echo -e "  ${GREEN}✓${NC} ANTHROPIC_API_KEY set"
fi

if [[ $ERRORS -gt 0 ]]; then
    echo ""
    echo -e "${RED}Found $ERRORS critical errors. Fix them and try again.${NC}"
    exit 1
fi

# ---------------------------------------------------------------------------
# Install Python dependencies
# ---------------------------------------------------------------------------
echo ""
echo "Installing Python dependencies..."
pip3 install -q -r requirements.txt 2>/dev/null || pip install -q -r requirements.txt

# Install screen capture dependencies
if [[ -d "$SCRIPT_DIR/capture" ]]; then
    echo "Installing screen capture dependencies..."
    (cd "$SCRIPT_DIR/capture" && npm install --silent 2>/dev/null) || echo -e "  ${YELLOW}⚠${NC} Capture deps install failed — GIFs will be skipped"
fi

# ---------------------------------------------------------------------------
# Launch
# ---------------------------------------------------------------------------
echo ""
echo -e "${CYAN}═══════════════════════════════════════════════════${NC}"
echo -e "${GREEN}🚀 Launching orchestrator...${NC}"
echo -e "${CYAN}═══════════════════════════════════════════════════${NC}"
echo ""
echo "  Workspace: ${WORKSPACE:-~/auto-deployer-workspace}"
echo "  GitHub:    ${GITHUB_USERNAME:-Mathew-Harvey}"
echo "  Log file:  ${WORKSPACE:-~/auto-deployer-workspace}/orchestrator.log"
echo ""
echo "  The orchestrator will process all 132 repos."
echo "  Estimated time: 2-6 hours depending on project complexity."
echo "  Progress is saved after each repo — safe to interrupt and resume."
echo ""

# Run with nohup so it survives terminal disconnection
if [[ "${BACKGROUND:-false}" == "true" ]]; then
    echo "  Running in background. Check logs with:"
    echo "  tail -f ${WORKSPACE:-~/auto-deployer-workspace}/orchestrator.log"
    echo ""
    nohup python3 orchestrator.py > /dev/null 2>&1 &
    echo -e "  ${GREEN}PID: $!${NC}"
else
    python3 orchestrator.py
fi