# Autonomous Repo Deployer

Fully autonomous pipeline that clones all 132 of your GitHub repos, uses Claude Code to analyse and complete each project, deploys them to Render.com, captures screen recording GIFs of each live site, and generates a unified portfolio site.

## What It Does

For **each repository**:
1. **Check if already live** — scans GitHub Pages, Render, and any URLs in repo metadata. If it's already deployed and working, **it does not edit the repo** — just collects metadata for the portfolio.
2. **Clone** — pulls the repo locally
3. **Analyse** — Claude Code headless inspects the codebase and determines if it's deployable, ambiguous, or should be skipped
4. **Complete** — Claude Code fixes bugs, adds missing dependencies, creates build configs, adds `render.yaml`
5. **Push** — commits changes back to GitHub
6. **Deploy** — creates a Render.com service via their API
7. **Log** — records everything in `manifest.json`

**After all repos are processed**:
1. **Screen Capture** — Puppeteer visits each deployed site, scrolls through it, and records an animated GIF
2. **Portfolio Site** — generates and deploys a portfolio website with GIF previews, category filters, and live/new-deploy badges

## Key Behaviour: Already Live = Don't Touch

If a repo is already deployed somewhere (GitHub Pages, Render, custom domain), the orchestrator:
- Detects it's live by checking candidate URLs
- Collects metadata (tech stack, category, description) via a light analysis
- Includes it in the portfolio with a green "Live" badge
- Does **NOT** edit, commit, push, or redeploy anything

This protects your working projects while still showcasing them.

## Skip Rules

Projects are **automatically skipped** if:
- Fork repos
- Archived repos
- Empty repos (0 size)
- Claude's analysis says `is_ambiguous: true`
- Confidence score below 40%
- Not deployable (libraries, CLI tools without web UI)

## Screen Capture GIFs

The pipeline uses Puppeteer + ffmpeg to automatically record each deployed site:
- Opens the site in headless Chrome (1280x800)
- Pauses on the hero section
- Smooth-scrolls through the full page
- Pauses at the bottom, scrolls back up
- Captures at 8fps for 6 seconds
- Assembles into an optimised GIF (capped at 5MB)
- GIFs are lazy-loaded on the portfolio site via IntersectionObserver

## Prerequisites

| Tool | Install | Required? |
|------|---------|-----------|
| Python 3.10+ | `brew install python` | Yes |
| Node.js 18+ | `brew install node` | Yes |
| Git | `brew install git` | Yes |
| Claude Code | `npm install -g @anthropic-ai/claude-code` then `claude auth login` | Yes |
| ffmpeg | `brew install ffmpeg` / `sudo apt install ffmpeg` | For GIFs |
| Chromium | Installed automatically by Puppeteer | For GIFs |

## API Keys Needed

1. **GitHub Personal Access Token** — https://github.com/settings/tokens (scopes: repo, read:user)
2. **Render API Key** — https://dashboard.render.com/u/settings/api-keys
3. **Anthropic API Key** — https://console.anthropic.com/

## Setup

```bash
cd auto-deployer
cp .env.template .env
nano .env   # Add your 3 API keys
chmod +x run.sh
```

## Usage

```bash
./run.sh                    # Full send (autonomous)
./run.sh --dry-run          # Analyse only, no changes
BACKGROUND=true ./run.sh    # Run in background
SKIP_EXISTING=false ./run.sh  # Force reprocess everything
```

Monitor progress: `tail -f ~/auto-deployer-workspace/orchestrator.log`

Safe to interrupt and resume — progress saved after each repo.

## Output

```
~/auto-deployer-workspace/
├── manifest.json              # Full record of every repo
├── orchestrator.log           # Execution log
├── repos/                     # All cloned repositories
├── gifs/                      # Screen capture GIFs
└── mat-harvey-portfolio/      # Generated portfolio site
    ├── index.html
    ├── render.yaml
    └── gifs/
```

## Estimated Runtime

- 132 repos x ~3-5 min average = 6-11 hours
- Already-live sites: ~30 sec each (URL check + light analysis)
- GIF capture phase: ~30 sec per deployed site
- Safe to run overnight

## Troubleshooting

- **Claude Code not found**: `npm install -g @anthropic-ai/claude-code && claude auth login`
- **GIFs not generating**: Install ffmpeg
- **Redeploy one project**: Delete its entry from manifest.json and re-run
- **Logs**: `tail -f ~/auto-deployer-workspace/orchestrator.log`