#!/usr/bin/env python3
"""
Autonomous GitHub Repo Deployer
================================
Fetches all repos from a GitHub account, uses Claude Code to complete each project,
deploys to Render.com, and builds a portfolio site linking everything together.

Usage:
    python orchestrator.py

Configuration via environment variables or .env file.
"""

import os
import json
import re
import subprocess
import time
import logging
import shutil
import sys
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional
from enum import Enum

import requests
from dotenv import load_dotenv

# Load .env file so this works cross-platform (Windows included)
load_dotenv()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

GITHUB_USERNAME = os.getenv("GITHUB_USERNAME", "Mathew-Harvey")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")  # optional, for private repos & higher rate limits
RENDER_API_KEY = os.getenv("RENDER_API_KEY", "")
RENDER_OWNER_ID = os.getenv("RENDER_OWNER_ID", "")
CLAUDE_MAX_TURNS = int(os.getenv("CLAUDE_MAX_TURNS", "30"))
WORKSPACE = Path(os.getenv("WORKSPACE", os.path.expanduser("~/auto-deployer-workspace")))
MANIFEST_FILE = WORKSPACE / "manifest.json"
LOG_FILE = WORKSPACE / "orchestrator.log"
SKIP_EXISTING = os.getenv("SKIP_EXISTING", "true").lower() == "true"
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"
PORTFOLIO_REPO_NAME = "mat-harvey-portfolio"

# Repos to always skip (forks, configs, profile READMEs, etc.)
SKIP_REPOS = {
    "mathew-harvey",  # profile README
    ".github",
}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

WORKSPACE.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(
            open(sys.stdout.fileno(), mode="w", encoding="utf-8", closefd=False)
        ),
    ],
)
log = logging.getLogger("orchestrator")


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

class ProjectStatus(str, Enum):
    PENDING = "pending"
    ANALYSING = "analysing"
    SKIPPED = "skipped"
    COMPLETED = "completed"
    DEPLOYED = "deployed"
    ALREADY_LIVE = "already-live"  # was already deployed & working — don't touch
    FAILED = "failed"


@dataclass
class Project:
    name: str
    github_url: str
    description: str = ""
    language: str = ""
    is_fork: bool = False
    status: str = ProjectStatus.PENDING.value
    deploy_url: str = ""
    render_service_id: str = ""
    tech_stack: list = field(default_factory=list)
    category: str = ""
    skip_reason: str = ""
    error: str = ""
    completed_at: str = ""
    gif_url: str = ""  # relative path to the screen capture GIF
    existing_deploy_urls: list = field(default_factory=list)  # known URLs to check


# ---------------------------------------------------------------------------
# Manifest persistence
# ---------------------------------------------------------------------------

def load_manifest() -> dict:
    if MANIFEST_FILE.exists():
        with open(MANIFEST_FILE) as f:
            return json.load(f)
    return {"projects": {}, "portfolio_url": "", "last_run": ""}


def save_manifest(manifest: dict):
    manifest["last_run"] = datetime.utcnow().isoformat()
    with open(MANIFEST_FILE, "w") as f:
        json.dump(manifest, f, indent=2)


# ---------------------------------------------------------------------------
# GitHub API
# ---------------------------------------------------------------------------

def fetch_all_repos() -> list[dict]:
    """Fetch all public repos for the user via GitHub API."""
    headers = {"Accept": "application/vnd.github.v3+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"token {GITHUB_TOKEN}"

    repos = []
    page = 1
    while True:
        url = f"https://api.github.com/users/{GITHUB_USERNAME}/repos?per_page=100&page={page}&sort=updated"
        log.info(f"Fetching repos page {page}...")
        resp = requests.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        if not data:
            break
        repos.extend(data)
        page += 1
        time.sleep(0.5)  # be nice to rate limits

    log.info(f"Found {len(repos)} repositories.")
    return repos


def clone_repo(repo_url: str, dest: Path):
    """Clone a repo. If it exists, pull latest."""
    if dest.exists():
        log.info(f"  Pulling latest for {dest.name}...")
        subprocess.run(["git", "-C", str(dest), "pull", "--ff-only"],
                        capture_output=True, shell=True)
    else:
        log.info(f"  Cloning {repo_url}...")
        clone_url = repo_url
        if GITHUB_TOKEN:
            clone_url = repo_url.replace("https://", f"https://{GITHUB_TOKEN}@")
        subprocess.run(["git", "clone", "--depth", "1", clone_url, str(dest)],
                        check=True, capture_output=True, shell=True)


# ---------------------------------------------------------------------------
# Live Site Detection
# ---------------------------------------------------------------------------

# Common deployment URL patterns to check for each repo
def get_candidate_urls(repo: dict) -> list[str]:
    """Generate candidate URLs where a repo might already be deployed."""
    name = repo["name"]
    username = GITHUB_USERNAME.lower()
    urls = []

    # GitHub Pages
    urls.append(f"https://{username}.github.io/{name}/")
    urls.append(f"https://{username}.github.io/{name}")

    # Existing Render deployments
    urls.append(f"https://mh-{name[:30]}.onrender.com")
    urls.append(f"https://{name}.onrender.com")

    # Check repo homepage field
    homepage = repo.get("homepage", "")
    if homepage and homepage.startswith("http"):
        urls.append(homepage)

    # Check description for URLs
    desc = repo.get("description", "") or ""
    found_urls = re.findall(r'https?://[^\s<>"]+', desc)
    urls.extend(found_urls)

    return list(dict.fromkeys(urls))  # dedupe preserving order


def check_url_live(url: str, timeout: int = 12) -> bool:
    """Check if a URL returns a live, non-error page."""
    try:
        resp = requests.get(url, timeout=timeout, allow_redirects=True, headers={
            "User-Agent": "Mozilla/5.0 (Auto-Deployer/1.0)"
        })
        if resp.status_code >= 400:
            return False

        # Check for meaningful content (not blank or error pages)
        body = resp.text[:2000].lower()
        if len(body.strip()) < 100:
            return False

        error_signals = ["404", "not found", "page not found", "site not found",
                         "suspended", "there isn't a github pages site",
                         "this page could not be found", "404.html"]
        if any(sig in body for sig in error_signals):
            return False

        return True

    except (requests.RequestException, Exception):
        return False


def find_existing_deployment(repo: dict) -> tuple[bool, str]:
    """Check if this repo is already deployed and live somewhere."""
    candidates = get_candidate_urls(repo)
    log.info(f"  Checking {len(candidates)} candidate URLs for existing deployment...")

    for url in candidates:
        if check_url_live(url):
            log.info(f"  ✅ Already live at: {url}")
            return True, url

    return False, ""


# ---------------------------------------------------------------------------
# GitHub API File-Tree Classification (NO Claude Code needed)
# ---------------------------------------------------------------------------

def classify_repo_from_api(repo: dict) -> dict:
    """
    Classify a repo using GitHub API metadata and file tree.
    Returns tier (0/1/2), category, tech_stack, deploy_type — zero AI cost.
    """
    headers = {"Accept": "application/vnd.github.v3+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"token {GITHUB_TOKEN}"

    name = repo["name"]
    language = (repo.get("language") or "").lower()
    description = repo.get("description") or ""

    # Fetch the file tree from GitHub API
    files = set()
    try:
        tree_url = f"https://api.github.com/repos/{GITHUB_USERNAME}/{name}/git/trees/main?recursive=1"
        resp = requests.get(tree_url, headers=headers, timeout=10)
        if resp.status_code == 404:
            # Try 'master' branch
            tree_url = f"https://api.github.com/repos/{GITHUB_USERNAME}/{name}/git/trees/master?recursive=1"
            resp = requests.get(tree_url, headers=headers, timeout=10)
        if resp.status_code == 200:
            tree = resp.json().get("tree", [])
            files = {item["path"].lower() for item in tree if item["type"] == "blob"}
    except Exception as e:
        log.warning(f"  Could not fetch file tree for {name}: {e}")

    # Derive file-based signals
    has_index_html = any(f.endswith("index.html") for f in files)
    has_package_json = "package.json" in files
    has_requirements = "requirements.txt" in files
    has_dockerfile = "dockerfile" in files or "Dockerfile" in files
    has_render_yaml = "render.yaml" in files
    has_readme = "readme.md" in files or "README.md" in files
    has_html_files = any(f.endswith(".html") for f in files)
    has_js_files = any(f.endswith(".js") or f.endswith(".jsx") or f.endswith(".ts") or f.endswith(".tsx") for f in files)
    has_py_files = any(f.endswith(".py") for f in files)
    has_cs_files = any(f.endswith(".cs") or f.endswith(".csproj") for f in files)
    has_react = any("react" in f for f in files) or any(f.endswith(".jsx") or f.endswith(".tsx") for f in files)
    has_server = any(n in files for n in ("server.js", "app.js", "index.js", "server.py", "app.py", "main.py"))
    file_count = len(files)

    # Build tech stack from file evidence
    tech_stack = []
    if has_html_files:
        tech_stack.append("HTML")
    if any(f.endswith(".css") for f in files):
        tech_stack.append("CSS")
    if has_js_files:
        tech_stack.append("JavaScript")
    if has_react:
        tech_stack.append("React")
    if has_py_files:
        tech_stack.append("Python")
    if has_cs_files:
        tech_stack.append("C#")
    if language and language.capitalize() not in [t.lower() for t in tech_stack]:
        tech_stack.insert(0, language.capitalize())

    # Determine deploy type
    if has_cs_files:
        deploy_type = "docker"
    elif has_py_files and has_server:
        deploy_type = "python"
    elif has_package_json and has_server:
        deploy_type = "node"
    elif has_package_json and has_react:
        deploy_type = "node"  # React app needs build
    elif has_html_files or has_index_html:
        deploy_type = "static"
    elif has_package_json:
        deploy_type = "node"
    elif has_py_files:
        deploy_type = "python"
    else:
        deploy_type = "static"

    # Determine category
    if has_react or (has_package_json and has_server):
        category = "web-app"
    elif has_server and not has_html_files:
        category = "api"
    elif has_html_files and not has_package_json:
        category = "static-site"
    elif has_py_files and not has_html_files:
        category = "cli-tool"
    elif has_cs_files:
        category = "api"
    elif language in ("html", "css", "javascript"):
        category = "static-site"
    else:
        category = "other"

    # ── Determine tier ────────────────────────────────────────────────
    # Tier 0: Simple static HTML/CSS/JS — just add render.yaml, no AI
    # Tier 1: Needs light AI (README + minor fixes) — 15 turns
    # Tier 2: Complex, needs real completion — 30 turns

    if file_count <= 2:
        tier = 2  # Almost empty, needs real work
    elif deploy_type == "static" and has_index_html and not has_package_json:
        tier = 0  # Pure HTML site — no AI needed
    elif deploy_type == "static" and has_html_files and not has_package_json:
        tier = 0  # Static files — no AI needed
    elif has_render_yaml and has_readme and has_package_json:
        tier = 1  # Mostly ready, just needs polish
    elif has_cs_files or has_dockerfile:
        tier = 2  # Complex — needs Docker/real work
    elif has_package_json and not has_server and not has_react:
        tier = 2  # Node project but unclear what it does
    else:
        tier = 1  # Default: light AI to polish

    log.info(f"  Classified: tier={tier}, deploy_type={deploy_type}, category={category}, "
             f"files={file_count}, has_index={has_index_html}, has_pkg={has_package_json}")

    return {
        "tier": tier,
        "deploy_type": deploy_type,
        "category": category,
        "tech_stack": tech_stack,
        "has_index_html": has_index_html,
        "has_package_json": has_package_json,
        "has_readme": has_readme,
        "has_render_yaml": has_render_yaml,
        "has_server": has_server,
        "file_count": file_count,
    }


def quick_fix_static_repo(project_dir: Path, project_name: str):
    """
    Tier 0: Add render.yaml to a simple static HTML repo. No Claude Code needed.
    Also ensures a basic README exists.
    """
    render_yaml = project_dir / "render.yaml"
    if not render_yaml.exists():
        render_yaml.write_text(f"""services:
  - type: web
    name: {project_name}
    runtime: static
    staticPublishPath: ./
""")
        log.info(f"  Added render.yaml (static)")

    readme = project_dir / "README.md"
    if not readme.exists():
        readme.write_text(f"# {project_name}\n\nA web project by Mathew Harvey.\n")
        log.info(f"  Added basic README.md")

    # Git commit
    try:
        subprocess.run(["git", "-C", str(project_dir), "add", "-A"],
                        capture_output=True, shell=True)
        subprocess.run(["git", "-C", str(project_dir), "commit", "-m",
                        "auto: add render.yaml for deployment"],
                        capture_output=True, shell=True)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Claude Code Integration
# ---------------------------------------------------------------------------

TIER1_PROMPT = """You are preparing this project for deployment. Be QUICK and FOCUSED.

DO THESE THINGS ONLY:
1. Read the project to understand what it does
2. Write a GOOD README.md — clear title, description, tech stack, how to run it
3. If render.yaml is missing, add one (static site unless it clearly needs a server)
4. Fix any OBVIOUS bugs (missing files, broken imports) but don't rewrite the project
5. Commit with message "auto: polish and prepare for deployment"

For render.yaml, use:
services:
  - type: web
    name: {project_name}
    runtime: static
    staticPublishPath: ./

Do NOT spend time on big refactors. Just make it presentable and deployable.
"""

TIER2_PROMPT = """You are an autonomous coding agent. Make this project WORK as a deployed web application.

BE AMBITIOUS but EFFICIENT. You have limited turns.

PRIORITIES (in order):
1. Read and understand the project purpose
2. Write a GOOD README.md — clear title, description, features, tech stack
3. Fix ALL bugs, missing imports, broken dependencies
4. If it's Node/React: fix package.json, ensure build works
5. If it's Python: fix requirements.txt, ensure it runs
6. If it's plain HTML/CSS/JS: ensure index.html works
7. Make it VISUALLY PRESENTABLE
8. Add render.yaml for deployment:

For static sites:
services:
  - type: web
    name: PROJECT_NAME
    runtime: static
    staticPublishPath: ./

For Node servers:
services:
  - type: web
    name: PROJECT_NAME
    runtime: node
    buildCommand: npm install
    startCommand: node server.js
    plan: free

9. Commit ALL changes with message "auto: complete and prepare for deployment"

Do NOT give up. Make it work.
"""


def run_claude_code(project_dir: Path, prompt: str, max_turns: int = None) -> tuple[bool, str]:
    """Run Claude Code in headless mode against a project directory."""
    if max_turns is None:
        max_turns = CLAUDE_MAX_TURNS

    cmd = [
        "claude",
        "-p", prompt,
        "--allowedTools", "Edit,Bash,Write,Read",
        "--max-turns", str(max_turns),
        "--output-format", "text",
    ]

    log.info(f"  Running Claude Code (max {max_turns} turns)...")

    try:
        result = subprocess.run(
            cmd,
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",           # replace un-decodable bytes instead of crashing
            timeout=600,                # 10 minute timeout per project
            shell=True,                 # needed on Windows to find .cmd/.ps1 CLIs
        )

        output = (result.stdout or "") + (result.stderr or "")
        success = result.returncode == 0

        if not success:
            log.warning(f"  Claude Code exited with code {result.returncode}")

        return success, output

    except subprocess.TimeoutExpired:
        log.warning("  Claude Code timed out (10 min)")
        return False, "TIMEOUT"
    except FileNotFoundError:
        log.error("  Claude Code CLI not found. Install with: npm install -g @anthropic-ai/claude-code")
        return False, "CLI_NOT_FOUND"
    except Exception as e:
        log.error(f"  Claude Code unexpected error: {e}")
        return False, str(e)


def extract_json_from_output(output: str) -> dict | None:
    """Robustly extract a JSON object from Claude's output, handling markdown blocks etc."""
    if not output:
        return None

    # Strategy 1: Look for ```json ... ``` blocks
    json_block = re.search(r'```(?:json)?\s*\n?([\s\S]*?)\n?```', output)
    if json_block:
        try:
            return json.loads(json_block.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Strategy 2: Find all { ... } candidates, try largest first
    candidates = []
    depth = 0
    start = -1
    for i, ch in enumerate(output):
        if ch == '{':
            if depth == 0:
                start = i
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0 and start >= 0:
                candidates.append(output[start:i + 1])
                start = -1

    # Try longest candidate first (most likely the full JSON)
    for candidate in sorted(candidates, key=len, reverse=True):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue

    return None


def complete_project_tiered(project_dir: Path, tier: int, project_name: str) -> bool:
    """
    Use Claude Code to complete a project, with effort scaled by tier.
    Tier 0: Should not call this (handled by quick_fix_static_repo).
    Tier 1: Light polish (README + render.yaml + minor fixes) — 15 turns.
    Tier 2: Full completion — 30 turns.
    """
    if tier <= 0:
        log.warning(f"  complete_project_tiered called for tier 0 — skipping AI")
        return True

    if tier == 1:
        prompt = TIER1_PROMPT.replace("{project_name}", project_name)
        max_turns = 15
    else:
        prompt = TIER2_PROMPT.replace("PROJECT_NAME", project_name)
        max_turns = 30

    log.info(f"  Claude Code: tier {tier}, max {max_turns} turns")
    success, output = run_claude_code(project_dir, prompt, max_turns=max_turns)
    return success


# ---------------------------------------------------------------------------
# Render.com Deployment
# ---------------------------------------------------------------------------

def build_render_payload(project: Project, project_dir: Path) -> dict:
    """Build the correct Render API payload from project config."""
    import yaml

    render_yaml = project_dir / "render.yaml"
    service_type = "static_site"
    build_command = ""
    start_command = ""
    publish_path = "./"
    runtime = "node"

    if render_yaml.exists():
        with open(render_yaml) as f:
            config = yaml.safe_load(f)
        if config and "services" in config:
            svc = config["services"][0]
            if svc.get("type") == "web" and svc.get("runtime") not in ("static", None):
                service_type = "web_service"
                runtime = svc.get("runtime", "node")
                build_command = svc.get("buildCommand", "npm install")
                start_command = svc.get("startCommand", "npm start")
            else:
                build_command = svc.get("buildCommand", "")
                publish_path = svc.get("staticPublishPath", "./")

    service_name = f"mh-{project.name[:30]}"

    # Render API v1 requires serviceDetails nested object
    # For non-static, non-docker: build/start commands go inside envSpecificDetails
    if service_type == "static_site":
        payload = {
            "type": "static_site",
            "name": service_name,
            "ownerId": RENDER_OWNER_ID,
            "repo": project.github_url,
            "autoDeploy": "yes",
            "branch": "main",
            "serviceDetails": {
                "buildCommand": build_command,
                "publishPath": publish_path,
            },
        }
    else:
        payload = {
            "type": "web_service",
            "name": service_name,
            "ownerId": RENDER_OWNER_ID,
            "repo": project.github_url,
            "autoDeploy": "yes",
            "branch": "main",
            "serviceDetails": {
                "envSpecificDetails": {
                    "buildCommand": build_command,
                    "startCommand": start_command,
                },
                "plan": "free",
                "runtime": runtime,
            },
        }

    return payload


def deploy_to_render(project: Project, project_dir: Path) -> tuple[Optional[str], str]:
    """
    Deploy to Render using the Render API.
    Returns (deploy_url, error_message). error_message is "" on success.
    """
    if not RENDER_API_KEY:
        return None, "No RENDER_API_KEY set"

    headers = {
        "Authorization": f"Bearer {RENDER_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = build_render_payload(project, project_dir)
    service_name = payload["name"]

    log.info(f"  Deploying to Render as {payload['type']}: {service_name}")

    if DRY_RUN:
        log.info("  DRY RUN — skipping actual deployment")
        return f"https://{service_name}.onrender.com", ""

    # Retry with backoff for rate limits
    for api_attempt in range(3):
        try:
            resp = requests.post("https://api.render.com/v1/services", headers=headers, json=payload)
            if resp.status_code in (200, 201):
                deploy_url = f"https://{service_name}.onrender.com"
                log.info(f"  ✅ Deployed: {deploy_url}")
                return deploy_url, ""
            elif resp.status_code == 429:
                wait_time = 30 * (api_attempt + 1)
                log.warning(f"  Render rate limit hit, waiting {wait_time}s...")
                time.sleep(wait_time)
                continue
            else:
                error_text = resp.text[:500]
                log.warning(f"  Render API error {resp.status_code}: {error_text}")
                return None, f"Render API {resp.status_code}: {error_text}"
        except Exception as e:
            log.error(f"  Render deployment failed: {e}")
            return None, str(e)

    log.warning(f"  Render rate limit persisted after retries")
    return None, "Rate limit exceeded after retries"


def deploy_with_retry(project: Project, project_dir: Path, max_retries: int = 2) -> Optional[str]:
    """
    Try to deploy. If Render returns an error, feed it back to Claude Code
    so it can fix the project config and retry.
    """
    for attempt in range(1, max_retries + 1):
        deploy_url, error = deploy_to_render(project, project_dir)

        if deploy_url:
            return deploy_url

        if not error or attempt >= max_retries:
            break

        # Don't waste Claude Code credits on rate limit or network errors
        if "rate limit" in error.lower() or "Rate limit" in error:
            log.warning(f"  Rate limit — skipping self-healing retry")
            break

        # ── Self-healing: feed the error back to Claude Code ──────────
        log.info(f"  🔄 Retry {attempt}/{max_retries}: asking Claude to fix deployment config...")

        fix_prompt = f"""The Render.com deployment FAILED with this error:

{error}

Fix the project so it deploys successfully. Common issues:
- If "must include serviceDetails": the render.yaml might specify a non-static runtime but the project should be static. Change render.yaml to use runtime: static if it's just HTML/CSS/JS.
- If the build fails: fix the build command or dependencies.
- If it's a Node project, make sure package.json has valid "start" and "build" scripts.
- If it's truly a static site (HTML/CSS/JS only), use this render.yaml:

services:
  - type: web
    name: {project.name}
    runtime: static
    staticPublishPath: ./

After fixing, commit the changes with message "auto: fix deployment config".
"""
        success, _ = run_claude_code(project_dir, fix_prompt, max_turns=15)

        if success:
            # Push the fix
            push_changes(project_dir, project.name)
        else:
            log.warning(f"  Claude Code fix attempt failed")
            break

    return None


def push_changes(project_dir: Path, project_name: str):
    """Push Claude's changes back to GitHub."""
    try:
        subprocess.run(["git", "-C", str(project_dir), "add", "-A"],
                        check=True, capture_output=True, shell=True)
        subprocess.run(
            ["git", "-C", str(project_dir), "commit", "-m", "auto: complete and prepare for deployment"],
            capture_output=True, shell=True,
        )
        # Set the remote URL with token for auth
        if GITHUB_TOKEN:
            remote_url = f"https://{GITHUB_TOKEN}@github.com/{GITHUB_USERNAME}/{project_name}.git"
            subprocess.run(["git", "-C", str(project_dir), "remote", "set-url", "origin", remote_url],
                           capture_output=True, shell=True)
        subprocess.run(["git", "-C", str(project_dir), "push"],
                        check=True, capture_output=True, shell=True)
        log.info(f"  Pushed changes for {project_name}")
    except subprocess.CalledProcessError as e:
        log.warning(f"  Git push failed for {project_name}: {e}")


# ---------------------------------------------------------------------------
# Portfolio Site Generator
# ---------------------------------------------------------------------------

PORTFOLIO_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Mat Harvey — Developer Portfolio</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=Syne:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg: #0a0a0f;
            --surface: #12121a;
            --surface-hover: #1a1a28;
            --border: #2a2a3a;
            --text: #e8e8f0;
            --text-muted: #8888a0;
            --accent: #00e5a0;
            --accent-dim: #00e5a020;
            --accent-mid: #00e5a060;
            --orange: #ff6b35;
            --blue: #4dabf7;
            --live-green: #22c55e;
        }

        * { margin: 0; padding: 0; box-sizing: border-box; }

        body {
            font-family: 'Space Mono', monospace;
            background: var(--bg);
            color: var(--text);
            min-height: 100vh;
            overflow-x: hidden;
        }

        .noise {
            position: fixed;
            top: 0; left: 0; right: 0; bottom: 0;
            background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.03'/%3E%3C/svg%3E");
            pointer-events: none;
            z-index: 9999;
        }

        .gradient-orb {
            position: fixed;
            width: 600px;
            height: 600px;
            border-radius: 50%;
            filter: blur(120px);
            opacity: 0.15;
            pointer-events: none;
        }

        .orb-1 { top: -200px; right: -200px; background: var(--accent); }
        .orb-2 { bottom: -300px; left: -200px; background: var(--orange); }

        header {
            padding: 3rem 2rem 2rem;
            max-width: 1400px;
            margin: 0 auto;
            position: relative;
        }

        .header-top {
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            margin-bottom: 2rem;
        }

        h1 {
            font-family: 'Syne', sans-serif;
            font-size: clamp(2.5rem, 6vw, 4.5rem);
            font-weight: 800;
            line-height: 1;
            letter-spacing: -0.03em;
        }

        h1 span { color: var(--accent); }

        .subtitle {
            color: var(--text-muted);
            font-size: 0.85rem;
            margin-top: 0.75rem;
            line-height: 1.6;
        }

        .stats-bar {
            display: flex;
            gap: 2rem;
            padding: 1rem 0;
            border-top: 1px solid var(--border);
            border-bottom: 1px solid var(--border);
            margin-bottom: 2rem;
            flex-wrap: wrap;
        }

        .stat {
            display: flex;
            flex-direction: column;
        }

        .stat-value {
            font-family: 'Syne', sans-serif;
            font-size: 1.8rem;
            font-weight: 700;
            color: var(--accent);
        }

        .stat-label {
            font-size: 0.7rem;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.1em;
        }

        .filters {
            display: flex;
            gap: 0.5rem;
            flex-wrap: wrap;
            margin-bottom: 2rem;
            padding: 0 2rem;
            max-width: 1400px;
            margin-left: auto;
            margin-right: auto;
        }

        .filter-btn {
            background: var(--surface);
            border: 1px solid var(--border);
            color: var(--text-muted);
            padding: 0.5rem 1rem;
            border-radius: 2px;
            font-family: 'Space Mono', monospace;
            font-size: 0.75rem;
            cursor: pointer;
            transition: all 0.2s;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }

        .filter-btn:hover, .filter-btn.active {
            background: var(--accent-dim);
            border-color: var(--accent);
            color: var(--accent);
        }

        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(400px, 1fr));
            gap: 1px;
            background: var(--border);
            max-width: 1400px;
            margin: 0 auto 4rem;
            border: 1px solid var(--border);
        }

        .card {
            background: var(--surface);
            display: flex;
            flex-direction: column;
            transition: all 0.3s ease;
            position: relative;
            overflow: hidden;
        }

        .card::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            width: 3px;
            height: 0;
            background: var(--accent);
            transition: height 0.4s ease;
            z-index: 2;
        }

        .card:hover {
            background: var(--surface-hover);
        }

        .card:hover::before {
            height: 100%;
        }

        /* GIF Preview Area */
        .card-preview {
            position: relative;
            width: 100%;
            height: 200px;
            background: var(--bg);
            overflow: hidden;
        }

        /* Tone down visual noise; reveal motion detail on hover */
        .card-preview::after {
            content: '';
            position: absolute;
            inset: 0;
            background: rgba(8, 12, 22, 0.34);
            pointer-events: none;
            transition: opacity 0.25s ease;
        }

        .card:hover .card-preview::after {
            opacity: 0.08;
        }

        .card-preview img {
            width: 100%;
            height: 100%;
            object-fit: cover;
            object-position: top center;
            opacity: 0;
            transition: opacity 0.4s ease;
        }

        .card-preview img.loaded {
            opacity: 1;
        }

        .card-preview .preview-placeholder {
            position: absolute;
            top: 0; left: 0; right: 0; bottom: 0;
            display: flex;
            align-items: center;
            justify-content: center;
            color: var(--border);
            font-size: 2rem;
            font-family: 'Syne', sans-serif;
            letter-spacing: 0.1em;
            text-transform: uppercase;
            background: linear-gradient(135deg, var(--bg) 0%, var(--surface) 100%);
        }

        .card-preview .preview-placeholder .monogram {
            width: 60px;
            height: 60px;
            border: 2px solid var(--border);
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 0.9rem;
            color: var(--text-muted);
            border-radius: 2px;
        }

        .card:hover .card-preview img.loaded {
            opacity: 1;
        }

        /* Status badge */
        .card-status {
            position: absolute;
            top: 8px;
            right: 8px;
            z-index: 2;
            font-size: 0.6rem;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            padding: 0.2rem 0.5rem;
            border-radius: 1px;
            font-family: 'Space Mono', monospace;
        }

        .card-status.live {
            background: #22c55e20;
            color: var(--live-green);
            border: 1px solid #22c55e40;
        }

        .card-status.live::before {
            content: '';
            display: inline-block;
            width: 6px;
            height: 6px;
            background: var(--live-green);
            border-radius: 50%;
            margin-right: 4px;
            animation: pulse 2s ease-in-out infinite;
        }

        .card-status.new {
            background: var(--accent-dim);
            color: var(--accent);
            border: 1px solid var(--accent-mid);
        }

        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.3; }
        }

        .card-body {
            padding: 1.25rem 1.5rem 1.5rem;
            display: flex;
            flex-direction: column;
            flex: 1;
        }

        .card-header {
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            margin-bottom: 0.5rem;
        }

        .card-name {
            font-family: 'Syne', sans-serif;
            font-size: 1.1rem;
            font-weight: 600;
            color: var(--text);
        }

        .card-category {
            font-size: 0.6rem;
            text-transform: uppercase;
            letter-spacing: 0.1em;
            color: var(--accent);
            background: var(--accent-dim);
            padding: 0.2rem 0.5rem;
            border-radius: 1px;
            white-space: nowrap;
            flex-shrink: 0;
            margin-left: 0.5rem;
        }

        .card-desc {
            color: var(--text-muted);
            font-size: 0.78rem;
            line-height: 1.5;
            flex: 1;
            margin-bottom: 0.75rem;
        }

        .card-tech {
            display: flex;
            gap: 0.35rem;
            flex-wrap: wrap;
            margin-bottom: 0.75rem;
        }

        .tech-tag {
            font-size: 0.6rem;
            color: var(--text-muted);
            border: 1px solid var(--border);
            padding: 0.12rem 0.45rem;
            border-radius: 1px;
        }

        .card-links {
            display: flex;
            gap: 1rem;
            margin-top: auto;
            padding-top: 0.5rem;
            border-top: 1px solid var(--border);
        }

        .card-links a {
            font-size: 0.72rem;
            color: var(--text-muted);
            text-decoration: none;
            transition: color 0.2s;
            display: flex;
            align-items: center;
            gap: 0.3rem;
        }

        .card-links a:hover { color: var(--accent); }
        .card-links a::before { content: '→'; color: var(--accent); }

        footer {
            text-align: center;
            padding: 2rem;
            color: var(--text-muted);
            font-size: 0.75rem;
            border-top: 1px solid var(--border);
            max-width: 1400px;
            margin: 0 auto;
        }

        footer a { color: var(--accent); text-decoration: none; }

        @media (max-width: 768px) {
            .grid { grid-template-columns: 1fr; }
            header { padding: 2rem 1rem; }
            .filters { padding: 0 1rem; }
        }
    </style>
</head>
<body>
    <div class="noise"></div>
    <div class="gradient-orb orb-1"></div>
    <div class="gradient-orb orb-2"></div>

    <header>
        <div class="header-top">
            <div>
                <h1>Mat <span>Harvey</span></h1>
                <p class="subtitle">
                    Software Developer · Marine Scientist · Perth, Western Australia<br>
                    Building elegant solutions across maritime tech, web applications, and beyond.
                </p>
            </div>
        </div>
        <div class="stats-bar">
            <div class="stat">
                <span class="stat-value" id="total-count">0</span>
                <span class="stat-label">Projects Live</span>
            </div>
            <div class="stat">
                <span class="stat-value" id="already-live-count">0</span>
                <span class="stat-label">Already Deployed</span>
            </div>
            <div class="stat">
                <span class="stat-value" id="tech-count">0</span>
                <span class="stat-label">Technologies</span>
            </div>
            <div class="stat">
                <span class="stat-value" id="cat-count">0</span>
                <span class="stat-label">Categories</span>
            </div>
            <div class="stat">
                <span class="stat-value">132</span>
                <span class="stat-label">Total Repos</span>
            </div>
        </div>
    </header>

    <div class="filters" id="filters"></div>
    <div class="grid" id="project-grid"></div>

    <footer>
        <p>Autonomously deployed by an AI pipeline &middot;
           <a href="https://github.com/Mathew-Harvey" target="_blank">GitHub</a> &middot;
           Built GENERATED_DATE</p>
    </footer>

    <script>
    const projects = PROJECTS_JSON;

    // Populate stats
    document.getElementById('total-count').textContent = projects.length;
    document.getElementById('already-live-count').textContent =
        projects.filter(p => p.status === 'already-live').length;
    const allTech = [...new Set(projects.flatMap(p => p.tech_stack || []))];
    document.getElementById('tech-count').textContent = allTech.length;
    const allCats = [...new Set(projects.map(p => p.category).filter(Boolean))];
    document.getElementById('cat-count').textContent = allCats.length;

    // Build filters
    const filtersDiv = document.getElementById('filters');
    const allBtn = document.createElement('button');
    allBtn.className = 'filter-btn active';
    allBtn.textContent = 'All';
    allBtn.onclick = () => filterBy('all');
    filtersDiv.appendChild(allBtn);

    allCats.sort().forEach(cat => {
        const btn = document.createElement('button');
        btn.className = 'filter-btn';
        btn.textContent = cat;
        btn.dataset.category = cat;
        btn.onclick = () => filterBy(cat);
        filtersDiv.appendChild(btn);
    });

    function filterBy(category) {
        document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
        if (category === 'all') {
            document.querySelector('.filter-btn').classList.add('active');
        } else {
            document.querySelector(`[data-category="${category}"]`).classList.add('active');
        }

        document.querySelectorAll('.card').forEach(card => {
            if (category === 'all' || card.dataset.category === category) {
                card.style.display = '';
            } else {
                card.style.display = 'none';
            }
        });
    }

    // Render cards
    const grid = document.getElementById('project-grid');
    projects.forEach((p, i) => {
        const card = document.createElement('div');
        card.className = 'card';
        card.dataset.category = p.category || '';
        card.style.animationDelay = `${i * 0.05}s`;

        const techList = Array.isArray(p.tech_stack)
            ? p.tech_stack
            : (typeof p.tech_stack === 'string' && p.tech_stack ? [p.tech_stack] : []);
        const techHtml = techList
            .map(t => `<span class="tech-tag">${t}</span>`).join('');

        const liveLink = p.deploy_url
            ? `<a href="${p.deploy_url}" target="_blank">Live Site</a>` : '';

        const statusBadge = p.status === 'already-live'
            ? '<span class="card-status live">Live</span>'
            : '<span class="card-status new">New Deploy</span>';

        // GIF preview: use actual GIF if available, else show monogram placeholder
        const hasGif = p.gif_url && p.gif_url.length > 0;
        const monogram = p.name.replace(/[-_]/g, ' ').split(' ')
            .map(w => w[0] || '').join('').toUpperCase().slice(0, 3);

        const previewHtml = hasGif
            ? `<div class="card-preview">
                 ${statusBadge}
                 <div class="preview-placeholder"><div class="monogram">${monogram}</div></div>
                 <img data-src="${p.gif_url}" alt="${p.name} preview" loading="lazy">
               </div>`
            : `<div class="card-preview">
                 ${statusBadge}
                 <div class="preview-placeholder"><div class="monogram">${monogram}</div></div>
               </div>`;

        card.innerHTML = `
            ${previewHtml}
            <div class="card-body">
                <div class="card-header">
                    <span class="card-name">${p.name}</span>
                    <span class="card-category">${p.category || 'other'}</span>
                </div>
                <p class="card-desc">${p.description || 'No description'}</p>
                <div class="card-tech">${techHtml}</div>
                <div class="card-links">
                    <a href="${p.github_url}" target="_blank">Source</a>
                    ${liveLink}
                </div>
            </div>
        `;
        grid.appendChild(card);
    });

    // Lazy-load GIFs: only load when card is hovered or scrolled into view
    const observer = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                const img = entry.target.querySelector('img[data-src]');
                if (img) {
                    const markLoaded = () => {
                        img.classList.add('loaded');
                        const placeholder = entry.target.querySelector('.preview-placeholder');
                        if (placeholder) placeholder.style.display = 'none';
                    };
                    img.onload = markLoaded;
                    img.src = img.dataset.src;
                    if (img.complete) markLoaded();
                    img.removeAttribute('data-src');
                }
                observer.unobserve(entry.target);
            }
        });
    }, { rootMargin: '200px' });

    document.querySelectorAll('.card-preview').forEach(el => observer.observe(el));
    </script>
</body>
</html>"""


def generate_portfolio(manifest: dict):
    """Generate the portfolio site from the manifest."""
    projects = []
    for name, data in manifest["projects"].items():
        if data.get("status") in (
            ProjectStatus.DEPLOYED.value,
            ProjectStatus.COMPLETED.value,
            ProjectStatus.ALREADY_LIVE.value,
        ):
            project_data = dict(data)
            if not project_data.get("gif_url"):
                gif_candidate = WORKSPACE / "gifs" / f"{name}.gif"
                if gif_candidate.exists():
                    project_data["gif_url"] = f"gifs/{name}.gif"
            projects.append(project_data)

    # Sort by category then name
    projects.sort(key=lambda p: (p.get("category", "zzz"), p.get("name", "")))

    # Copy GIF files into portfolio directory
    portfolio_dir = WORKSPACE / PORTFOLIO_REPO_NAME
    portfolio_dir.mkdir(exist_ok=True)
    gifs_portfolio_dir = portfolio_dir / "gifs"
    gifs_portfolio_dir.mkdir(exist_ok=True)

    gifs_source = WORKSPACE / "gifs"
    if gifs_source.exists():
        for gif_file in gifs_source.glob("*.gif"):
            dest = gifs_portfolio_dir / gif_file.name
            shutil.copy2(gif_file, dest)
            log.info(f"  Copied GIF: {gif_file.name}")

    html = PORTFOLIO_HTML_TEMPLATE.replace(
        "PROJECTS_JSON", json.dumps(projects, indent=2)
    ).replace(
        "GENERATED_DATE", datetime.utcnow().strftime("%Y-%m-%d")
    )

    (portfolio_dir / "index.html").write_text(html, encoding="utf-8")

    # Create render.yaml for the portfolio itself
    render_yaml = """services:
  - type: web
    name: mat-harvey-portfolio
    runtime: static
    staticPublishPath: ./
    headers:
      - path: /*
        name: Cache-Control
        value: public, max-age=3600
"""
    (portfolio_dir / "render.yaml").write_text(render_yaml)

    log.info(f"Portfolio site generated at {portfolio_dir}")
    return portfolio_dir


# ---------------------------------------------------------------------------
# Main Orchestration Loop
# ---------------------------------------------------------------------------

def should_skip_repo(repo: dict) -> tuple[bool, str]:
    """Determine if a repo should be skipped."""
    name = repo["name"].lower()

    if name in SKIP_REPOS:
        return True, "In skip list"

    if repo.get("fork", False):
        return True, "Fork"

    if repo.get("archived", False):
        return True, "Archived"

    if repo.get("size", 0) == 0:
        return True, "Empty repo"

    return False, ""


def process_repo(repo: dict, manifest: dict) -> Project:
    """Process a single repository through the tiered pipeline."""
    name = repo["name"]
    project_dir = WORKSPACE / "repos" / name

    # Check if already processed — but DON'T skip previously-skipped repos (we want to retry them)
    if SKIP_EXISTING and name in manifest["projects"]:
        existing = manifest["projects"][name]
        if existing.get("status") in (
            ProjectStatus.DEPLOYED.value,
            ProjectStatus.ALREADY_LIVE.value,
        ):
            log.info(f"⏭  Skipping {name} (already {existing['status']})")
            return Project(**{k: v for k, v in existing.items() if k in Project.__dataclass_fields__})

    project = Project(
        name=name,
        github_url=repo["html_url"],
        description=repo.get("description", "") or "",
        language=repo.get("language", "") or "",
        is_fork=repo.get("fork", False),
    )

    # Check basic skip conditions
    skip, reason = should_skip_repo(repo)
    if skip:
        project.status = ProjectStatus.SKIPPED.value
        project.skip_reason = reason
        log.info(f"⏭  Skipping {name}: {reason}")
        return project

    # ── Phase 0: Check if already deployed and live ──────────────────────
    is_live, live_url = find_existing_deployment(repo)
    if is_live:
        project.status = ProjectStatus.ALREADY_LIVE.value
        project.deploy_url = live_url
        project.completed_at = datetime.utcnow().isoformat()

        # Get metadata from GitHub API (no Claude Code!)
        classification = classify_repo_from_api(repo)
        project.tech_stack = classification["tech_stack"] or ([project.language] if project.language else [])
        project.category = classification["category"] or "web-app"
        if not project.description:
            project.description = repo.get("description") or name

        log.info(f"✅ {name} — already live at {live_url} (no edits made)")
        return project

    # ── Phase 1: Classify via GitHub API (zero AI cost) ──────────────────
    log.info(f"🔍 Classifying {name} via GitHub API...")
    classification = classify_repo_from_api(repo)

    tier = classification["tier"]
    project.tech_stack = classification["tech_stack"] or ([project.language] if project.language else [])
    project.category = classification["category"] or "other"
    if not project.description:
        project.description = repo.get("description") or name

    log.info(f"  Tier {tier} | {classification['deploy_type']} | {classification['category']} | "
             f"{classification['file_count']} files")

    # Skip truly empty repos (only README or nothing)
    if classification["file_count"] <= 1:
        project.status = ProjectStatus.SKIPPED.value
        project.skip_reason = "Empty or README-only repo"
        log.info(f"⏭  Skipping {name}: {project.skip_reason}")
        return project

    # In DRY_RUN mode, just catalog — don't clone, complete, or deploy
    if DRY_RUN:
        project.status = ProjectStatus.COMPLETED.value
        project.completed_at = datetime.utcnow().isoformat()
        log.info(f"📋 DRY RUN cataloged: {name} (tier {tier})")
        return project

    # ── Phase 2: Clone ───────────────────────────────────────────────────
    try:
        (WORKSPACE / "repos").mkdir(parents=True, exist_ok=True)
        clone_repo(repo["clone_url"], project_dir)
    except Exception as e:
        project.status = ProjectStatus.FAILED.value
        project.error = f"Clone failed: {e}"
        log.error(f"❌ Clone failed for {name}: {e}")
        return project

    # ── Phase 3: Complete (tiered) ───────────────────────────────────────
    if tier == 0:
        log.info(f"⚡ Tier 0 — quick-fix (no AI)...")
        quick_fix_static_repo(project_dir, name)
    else:
        log.info(f"🔧 Tier {tier} — Claude Code completion...")
        success = complete_project_tiered(project_dir, tier, name)
        if not success:
            project.status = ProjectStatus.FAILED.value
            project.error = "Completion failed"
            log.warning(f"⚠️  Completion failed for {name}")
            return project

    # Push changes back to GitHub
    push_changes(project_dir, name)
    project.status = ProjectStatus.COMPLETED.value

    # ── Phase 4: Deploy (with self-healing retry) ──────────────────────
    log.info(f"🚀 Deploying {name}...")
    deploy_url = deploy_with_retry(project, project_dir)
    if deploy_url:
        project.deploy_url = deploy_url
        project.status = ProjectStatus.DEPLOYED.value
    else:
        log.warning(f"⚠️  Deployment skipped/failed for {name}")

    project.completed_at = datetime.utcnow().isoformat()
    log.info(f"✅ {name} — {project.status}")
    return project


def reset_manifest_for_retry(manifest: dict):
    """Reset all failed/pending/skipped repos back to pending so they get retried."""
    reset_count = 0
    for name, data in manifest.get("projects", {}).items():
        status = data.get("status", "")
        if status in (ProjectStatus.FAILED.value, ProjectStatus.PENDING.value,
                       ProjectStatus.SKIPPED.value, ProjectStatus.ANALYSING.value,
                       ProjectStatus.COMPLETED.value):
            data["status"] = ProjectStatus.PENDING.value
            data["error"] = ""
            data["skip_reason"] = ""
            reset_count += 1
    log.info(f"Reset {reset_count} repos to pending for retry")
    return manifest


def main():
    log.info("=" * 60)
    log.info("AUTONOMOUS REPO DEPLOYER — TIERED PIPELINE")
    log.info(f"GitHub: {GITHUB_USERNAME} | Target: Render.com")
    log.info(f"Dry Run: {DRY_RUN} | Skip Existing: {SKIP_EXISTING}")
    log.info(f"Max Claude Code turns: {CLAUDE_MAX_TURNS}")
    log.info("=" * 60)

    manifest = load_manifest()

    # Reset all failed/pending/skipped repos for a fresh retry
    manifest = reset_manifest_for_retry(manifest)
    save_manifest(manifest)

    # Fetch all repos
    repos = fetch_all_repos()

    # Process stats
    total = len(repos)
    processed = 0
    deployed = 0
    already_live = 0
    skipped = 0
    failed = 0

    for i, repo in enumerate(repos, 1):
        log.info(f"\n{'─' * 40}")
        log.info(f"[{i}/{total}] {repo['name']}")
        log.info(f"{'─' * 40}")

        project = process_repo(repo, manifest)
        manifest["projects"][project.name] = asdict(project)

        # Save after each repo (resume-safe)
        save_manifest(manifest)

        if project.status == ProjectStatus.DEPLOYED.value:
            deployed += 1
        elif project.status == ProjectStatus.ALREADY_LIVE.value:
            already_live += 1
        elif project.status == ProjectStatus.SKIPPED.value:
            skipped += 1
        elif project.status == ProjectStatus.FAILED.value:
            failed += 1

        processed += 1

    # ── GIF Capture Phase ────────────────────────────────────────────────
    log.info("\n" + "=" * 60)
    log.info("📸 SCREEN CAPTURE PHASE")
    log.info("=" * 60)

    gifs_dir = WORKSPACE / "gifs"
    gifs_dir.mkdir(exist_ok=True)
    capture_script = Path(__file__).parent / "capture" / "capture.mjs"

    if capture_script.exists():
        # Install capture dependencies if needed
        capture_dir = Path(__file__).parent / "capture"
        if not (capture_dir / "node_modules").exists():
            log.info("Installing capture dependencies...")
            subprocess.run(
                ["npm", "install"], cwd=str(capture_dir),
                capture_output=True, timeout=120, shell=True,
            )

        # Check ffmpeg is available (cross-platform)
        ffmpeg_ok = shutil.which("ffmpeg") is not None

        if ffmpeg_ok:
            log.info(f"Running batch screen capture → {gifs_dir}")
            try:
                result = subprocess.run(
                    ["node", str(capture_script),
                     "--manifest", str(MANIFEST_FILE),
                     "--output-dir", str(gifs_dir)],
                    capture_output=True, text=True,
                    encoding="utf-8", errors="replace",
                    timeout=3600, shell=True,
                )
                log.info(result.stdout[-1000:] if result.stdout else "No capture output")
                if result.stderr:
                    log.warning(f"Capture stderr: {result.stderr[-500:]}")
            except subprocess.TimeoutExpired:
                log.warning("GIF capture phase timed out (1 hour)")
            except Exception as e:
                log.warning(f"GIF capture failed: {e}")

            # Update manifest with GIF paths
            for name, data in manifest["projects"].items():
                gif_file = gifs_dir / f"{name}.gif"
                if gif_file.exists():
                    data["gif_url"] = f"gifs/{name}.gif"
                    log.info(f"  🎬 {name} → GIF captured")

            save_manifest(manifest)
        else:
            log.warning("ffmpeg not found — skipping GIF capture")
            log.warning("Install: brew install ffmpeg / sudo apt install ffmpeg")
    else:
        log.warning(f"Capture script not found at {capture_script} — skipping GIF capture")

    # Generate portfolio site
    log.info("\n" + "=" * 60)
    log.info("GENERATING PORTFOLIO SITE")
    log.info("=" * 60)

    portfolio_dir = generate_portfolio(manifest)

    # Deploy portfolio
    portfolio_project = Project(
        name=PORTFOLIO_REPO_NAME,
        github_url=f"https://github.com/{GITHUB_USERNAME}/{PORTFOLIO_REPO_NAME}",
        description="Unified portfolio showcasing all deployed projects",
        category="portfolio",
        tech_stack=["HTML", "CSS", "JavaScript"],
    )

    # Init git and push portfolio repo
    if not DRY_RUN and GITHUB_TOKEN:
        try:
            subprocess.run(["git", "-C", str(portfolio_dir), "init"], capture_output=True, shell=True)
            subprocess.run(["git", "-C", str(portfolio_dir), "add", "-A"], capture_output=True, shell=True)
            subprocess.run(
                ["git", "-C", str(portfolio_dir), "commit", "-m", "auto: portfolio site"],
                capture_output=True, shell=True,
            )
            # Create repo on GitHub
            headers = {
                "Authorization": f"token {GITHUB_TOKEN}",
                "Accept": "application/vnd.github.v3+json",
            }
            requests.post(
                "https://api.github.com/user/repos",
                headers=headers,
                json={"name": PORTFOLIO_REPO_NAME, "description": "Unified developer portfolio", "auto_init": False},
            )
            subprocess.run(
                ["git", "-C", str(portfolio_dir), "remote", "add", "origin",
                 f"https://{GITHUB_TOKEN}@github.com/{GITHUB_USERNAME}/{PORTFOLIO_REPO_NAME}.git"],
                capture_output=True, shell=True,
            )
            subprocess.run(["git", "-C", str(portfolio_dir), "push", "-u", "origin", "main", "--force"],
                           capture_output=True, shell=True)
            log.info("Portfolio pushed to GitHub")
        except Exception as e:
            log.warning(f"Portfolio git push failed: {e}")

    deploy_url, _ = deploy_to_render(portfolio_project, portfolio_dir)
    if deploy_url:
        manifest["portfolio_url"] = deploy_url

    save_manifest(manifest)

    # Final summary
    log.info("\n" + "=" * 60)
    log.info("🏁 RUN COMPLETE")
    log.info(f"   Processed:     {processed}")
    log.info(f"   Already Live:  {already_live}  (untouched)")
    log.info(f"   Deployed:      {deployed}  (completed & deployed)")
    log.info(f"   Skipped:       {skipped}")
    log.info(f"   Failed:        {failed}")
    if manifest.get("portfolio_url"):
        log.info(f"   Portfolio:     {manifest['portfolio_url']}")
    log.info("=" * 60)


if __name__ == "__main__":
    main()