#!/usr/bin/env python3
"""Generate the portfolio index.html from the manifest and captured GIFs."""

import json
import os
from datetime import datetime
from pathlib import Path

MANIFEST_PATH = Path(os.path.expanduser("~/auto-deployer-workspace/manifest.json"))
GIFS_DIR = Path("gifs")
OUTPUT_FILE = Path("index.html")

def main():
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    projects = manifest.get("projects", {})

    # Collect live/deployed projects
    live = []
    for name, data in sorted(projects.items()):
        status = data.get("status", "")
        url = data.get("deploy_url", "")
        if status in ("already-live", "deployed") and url:
            gif_file = GIFS_DIR / f"{name}.gif"
            live.append({
                "name": name,
                "deploy_url": url,
                "github_url": data.get("github_url", ""),
                "description": data.get("description", "") or name,
                "category": data.get("category", "other") or "other",
                "tech_stack": data.get("tech_stack", []),
                "status": status,
                "gif_url": f"gifs/{name}.gif" if gif_file.exists() else "",
            })

    # Count stats
    total_live = len(live)
    already_live = sum(1 for p in live if p["status"] == "already-live")
    all_tech = sorted(set(t for p in live for t in p["tech_stack"]))
    all_cats = sorted(set(p["category"] for p in live))
    projects_json = json.dumps(live, indent=2, ensure_ascii=False)
    generated_date = datetime.utcnow().strftime("%Y-%m-%d")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Mat Harvey — Developer Portfolio</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=Syne:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        :root {{
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
        }}

        * {{ margin: 0; padding: 0; box-sizing: border-box; }}

        body {{
            font-family: 'Space Mono', monospace;
            background: var(--bg);
            color: var(--text);
            min-height: 100vh;
            overflow-x: hidden;
        }}

        .noise {{
            position: fixed;
            top: 0; left: 0; right: 0; bottom: 0;
            background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.03'/%3E%3C/svg%3E");
            pointer-events: none;
            z-index: 9999;
        }}

        .gradient-orb {{
            position: fixed;
            width: 600px;
            height: 600px;
            border-radius: 50%;
            filter: blur(120px);
            opacity: 0.15;
            pointer-events: none;
        }}

        .orb-1 {{ top: -200px; right: -200px; background: var(--accent); }}
        .orb-2 {{ bottom: -300px; left: -200px; background: var(--orange); }}

        header {{
            padding: 3rem 2rem 2rem;
            max-width: 1400px;
            margin: 0 auto;
            position: relative;
        }}

        .header-top {{
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            margin-bottom: 2rem;
        }}

        h1 {{
            font-family: 'Syne', sans-serif;
            font-size: clamp(2.5rem, 6vw, 4.5rem);
            font-weight: 800;
            line-height: 1;
            letter-spacing: -0.03em;
        }}

        h1 span {{ color: var(--accent); }}

        .subtitle {{
            color: var(--text-muted);
            font-size: 0.85rem;
            margin-top: 0.75rem;
            line-height: 1.6;
        }}

        .stats-bar {{
            display: flex;
            gap: 2rem;
            padding: 1rem 0;
            border-top: 1px solid var(--border);
            border-bottom: 1px solid var(--border);
            margin-bottom: 2rem;
            flex-wrap: wrap;
        }}

        .stat {{
            display: flex;
            flex-direction: column;
        }}

        .stat-value {{
            font-family: 'Syne', sans-serif;
            font-size: 1.8rem;
            font-weight: 700;
            color: var(--accent);
        }}

        .stat-label {{
            font-size: 0.7rem;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.1em;
        }}

        .filters {{
            display: flex;
            gap: 0.5rem;
            flex-wrap: wrap;
            margin-bottom: 2rem;
            padding: 0 2rem;
            max-width: 1400px;
            margin-left: auto;
            margin-right: auto;
        }}

        .filter-btn {{
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
        }}

        .filter-btn:hover, .filter-btn.active {{
            background: var(--accent-dim);
            border-color: var(--accent);
            color: var(--accent);
        }}

        .grid {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(380px, 1fr));
            gap: 1px;
            background: var(--border);
            max-width: 1400px;
            margin: 0 auto 4rem;
            border: 1px solid var(--border);
        }}

        .card {{
            background: var(--surface);
            display: flex;
            flex-direction: column;
            transition: all 0.3s ease;
            position: relative;
            overflow: hidden;
        }}

        .card::before {{
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            width: 3px;
            height: 0;
            background: var(--accent);
            transition: height 0.4s ease;
            z-index: 2;
        }}

        .card:hover {{
            background: var(--surface-hover);
        }}

        .card:hover::before {{
            height: 100%;
        }}

        .card-preview {{
            position: relative;
            width: 100%;
            height: 220px;
            background: var(--bg);
            overflow: hidden;
        }}

        .card-preview img {{
            width: 100%;
            height: 100%;
            object-fit: cover;
            object-position: top center;
            opacity: 0;
            transition: opacity 0.4s ease;
        }}

        .card-preview img.loaded {{
            opacity: 1;
        }}

        .card-preview .preview-placeholder {{
            position: absolute;
            top: 0; left: 0; right: 0; bottom: 0;
            display: flex;
            align-items: center;
            justify-content: center;
            color: var(--border);
            background: linear-gradient(135deg, var(--bg) 0%, var(--surface) 100%);
        }}

        .card-preview .monogram {{
            width: 60px;
            height: 60px;
            border: 2px solid var(--border);
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 0.9rem;
            color: var(--text-muted);
            border-radius: 2px;
            font-family: 'Syne', sans-serif;
            font-weight: 700;
        }}

        .card-status {{
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
        }}

        .card-status.live {{
            background: #22c55e20;
            color: var(--live-green);
            border: 1px solid #22c55e40;
        }}

        .card-status.live::before {{
            content: '';
            display: inline-block;
            width: 6px;
            height: 6px;
            background: var(--live-green);
            border-radius: 50%;
            margin-right: 4px;
            animation: pulse 2s ease-in-out infinite;
        }}

        .card-status.new {{
            background: var(--accent-dim);
            color: var(--accent);
            border: 1px solid var(--accent-mid);
        }}

        @keyframes pulse {{
            0%, 100% {{ opacity: 1; }}
            50% {{ opacity: 0.3; }}
        }}

        .card-body {{
            padding: 1.25rem 1.5rem 1.5rem;
            display: flex;
            flex-direction: column;
            flex: 1;
        }}

        .card-header {{
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            margin-bottom: 0.5rem;
        }}

        .card-name {{
            font-family: 'Syne', sans-serif;
            font-size: 1.05rem;
            font-weight: 600;
            color: var(--text);
        }}

        .card-category {{
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
        }}

        .card-desc {{
            color: var(--text-muted);
            font-size: 0.75rem;
            line-height: 1.5;
            flex: 1;
            margin-bottom: 0.75rem;
            display: -webkit-box;
            -webkit-line-clamp: 3;
            -webkit-box-orient: vertical;
            overflow: hidden;
        }}

        .card-tech {{
            display: flex;
            gap: 0.35rem;
            flex-wrap: wrap;
            margin-bottom: 0.75rem;
        }}

        .tech-tag {{
            font-size: 0.6rem;
            color: var(--text-muted);
            border: 1px solid var(--border);
            padding: 0.12rem 0.45rem;
            border-radius: 1px;
        }}

        .card-links {{
            display: flex;
            gap: 1rem;
            margin-top: auto;
            padding-top: 0.5rem;
            border-top: 1px solid var(--border);
        }}

        .card-links a {{
            font-size: 0.72rem;
            color: var(--text-muted);
            text-decoration: none;
            transition: color 0.2s;
            display: flex;
            align-items: center;
            gap: 0.3rem;
        }}

        .card-links a:hover {{ color: var(--accent); }}
        .card-links a .arrow {{ color: var(--accent); }}

        footer {{
            text-align: center;
            padding: 2rem;
            color: var(--text-muted);
            font-size: 0.75rem;
            border-top: 1px solid var(--border);
            max-width: 1400px;
            margin: 0 auto;
        }}

        footer a {{ color: var(--accent); text-decoration: none; }}

        @media (max-width: 768px) {{
            .grid {{ grid-template-columns: 1fr; }}
            header {{ padding: 2rem 1rem; }}
            .filters {{ padding: 0 1rem; }}
        }}
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
                    Software Developer &middot; Marine Scientist &middot; Perth, Western Australia<br>
                    Building elegant solutions across maritime tech, web applications, and beyond.
                </p>
            </div>
        </div>
        <div class="stats-bar">
            <div class="stat">
                <span class="stat-value">{total_live}</span>
                <span class="stat-label">Projects Live</span>
            </div>
            <div class="stat">
                <span class="stat-value">{already_live}</span>
                <span class="stat-label">Previously Deployed</span>
            </div>
            <div class="stat">
                <span class="stat-value">{total_live - already_live}</span>
                <span class="stat-label">Newly Deployed</span>
            </div>
            <div class="stat">
                <span class="stat-value">{len(all_tech)}</span>
                <span class="stat-label">Technologies</span>
            </div>
            <div class="stat">
                <span class="stat-value">{len(all_cats)}</span>
                <span class="stat-label">Categories</span>
            </div>
        </div>
    </header>

    <div class="filters" id="filters"></div>
    <div class="grid" id="project-grid"></div>

    <footer>
        <p>Autonomously deployed by an AI pipeline &middot;
           <a href="https://github.com/Mathew-Harvey" target="_blank">GitHub</a> &middot;
           Built {generated_date}</p>
    </footer>

    <script>
    const projects = {projects_json};

    // Build category filters
    const allCats = [...new Set(projects.map(p => p.category).filter(Boolean))].sort();
    const filtersDiv = document.getElementById('filters');

    const allBtn = document.createElement('button');
    allBtn.className = 'filter-btn active';
    allBtn.textContent = 'All (' + projects.length + ')';
    allBtn.onclick = () => filterBy('all');
    filtersDiv.appendChild(allBtn);

    allCats.forEach(cat => {{
        const count = projects.filter(p => p.category === cat).length;
        const btn = document.createElement('button');
        btn.className = 'filter-btn';
        btn.textContent = cat + ' (' + count + ')';
        btn.dataset.category = cat;
        btn.onclick = () => filterBy(cat);
        filtersDiv.appendChild(btn);
    }});

    function filterBy(category) {{
        document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
        if (category === 'all') {{
            document.querySelector('.filter-btn').classList.add('active');
        }} else {{
            document.querySelector('[data-category="' + category + '"]').classList.add('active');
        }}
        document.querySelectorAll('.card').forEach(card => {{
            card.style.display = (category === 'all' || card.dataset.category === category) ? '' : 'none';
        }});
    }}

    // Render cards
    const grid = document.getElementById('project-grid');
    projects.forEach((p, i) => {{
        const card = document.createElement('div');
        card.className = 'card';
        card.dataset.category = p.category || '';

        const techHtml = (p.tech_stack || [])
            .map(t => '<span class="tech-tag">' + t + '</span>').join('');

        const liveLink = p.deploy_url
            ? '<a href="' + p.deploy_url + '" target="_blank"><span class="arrow">&#8594;</span> Live Site</a>' : '';

        const statusBadge = p.status === 'already-live'
            ? '<span class="card-status live">Live</span>'
            : '<span class="card-status new">New Deploy</span>';

        const hasGif = p.gif_url && p.gif_url.length > 0;
        const monogram = p.name.replace(/[-_]/g, ' ').split(' ')
            .map(w => (w[0] || '')).join('').toUpperCase().slice(0, 3);

        const displayName = p.name.replace(/[-_]/g, ' ');

        const previewHtml = hasGif
            ? '<div class="card-preview">' +
                statusBadge +
                '<div class="preview-placeholder"><div class="monogram">' + monogram + '</div></div>' +
                '<img data-src="' + p.gif_url + '" alt="' + displayName + ' preview" loading="lazy">' +
              '</div>'
            : '<div class="card-preview">' +
                statusBadge +
                '<div class="preview-placeholder"><div class="monogram">' + monogram + '</div></div>' +
              '</div>';

        card.innerHTML =
            previewHtml +
            '<div class="card-body">' +
                '<div class="card-header">' +
                    '<span class="card-name">' + displayName + '</span>' +
                    '<span class="card-category">' + (p.category || 'other') + '</span>' +
                '</div>' +
                '<p class="card-desc">' + (p.description || 'No description') + '</p>' +
                '<div class="card-tech">' + techHtml + '</div>' +
                '<div class="card-links">' +
                    '<a href="' + p.github_url + '" target="_blank"><span class="arrow">&#8594;</span> Source</a>' +
                    liveLink +
                '</div>' +
            '</div>';
        grid.appendChild(card);
    }});

    // Lazy-load GIF images
    const observer = new IntersectionObserver((entries) => {{
        entries.forEach(entry => {{
            if (entry.isIntersecting) {{
                const img = entry.target.querySelector('img[data-src]');
                if (img) {{
                    img.src = img.dataset.src;
                    img.onload = () => img.classList.add('loaded');
                    img.removeAttribute('data-src');
                }}
                observer.unobserve(entry.target);
            }}
        }});
    }}, {{ rootMargin: '300px' }});

    document.querySelectorAll('.card-preview').forEach(el => observer.observe(el));
    </script>
</body>
</html>"""

    OUTPUT_FILE.write_text(html, encoding="utf-8")
    print(f"Portfolio generated: {OUTPUT_FILE.absolute()}")
    print(f"  {total_live} projects, {len(all_cats)} categories, {len(all_tech)} technologies")
    print(f"  GIFs available for {sum(1 for p in live if p['gif_url'])} projects")


if __name__ == "__main__":
    main()
