#!/usr/bin/env node
/**
 * Batch Screen Capture — Automated GIF generator for deployed sites
 *
 * Reads the manifest, visits each deployed/already-live URL in headless Chrome,
 * scrolls through the page, captures frames, and assembles into optimised GIFs
 * using ffmpeg (two-pass palette method for quality).
 *
 * Usage:
 *   node capture.mjs --manifest /path/to/manifest.json --output-dir /path/to/gifs/
 */

import fs from "fs";
import path from "path";
import { execSync } from "child_process";
import puppeteer from "puppeteer";

// ---------------------------------------------------------------------------
// CLI args
// ---------------------------------------------------------------------------
const args = process.argv.slice(2);
function getArg(flag) {
  const idx = args.indexOf(flag);
  return idx !== -1 && idx + 1 < args.length ? args[idx + 1] : null;
}

const manifestPath = getArg("--manifest");
const outputDir = getArg("--output-dir");

if (!manifestPath || !outputDir) {
  console.error(
    "Usage: node capture.mjs --manifest <path> --output-dir <path>"
  );
  process.exit(1);
}

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------
const VIEWPORT = { width: 1280, height: 800 };
const FPS = 8;
const MAX_GIF_SIZE = 5 * 1024 * 1024; // 5 MB

// Phase durations (seconds)
const HERO_PAUSE = 2;
const SCROLL_DOWN = 2;
const BOTTOM_PAUSE = 1;
const SCROLL_UP = 1;

// ---------------------------------------------------------------------------
// Capture a single site
// ---------------------------------------------------------------------------
async function captureSite(browser, url, name) {
  const framesDir = path.join(outputDir, `_frames_${name}`);
  fs.mkdirSync(framesDir, { recursive: true });

  const page = await browser.newPage();
  await page.setViewport(VIEWPORT);

  try {
    await page.goto(url, { waitUntil: "networkidle2", timeout: 30_000 });
    // Let animations / lazy images settle
    await sleep(2000);

    const pageHeight = await page.evaluate(() => document.body.scrollHeight);
    const scrollDistance = Math.max(0, pageHeight - VIEWPORT.height);

    let idx = 0;
    const snap = async () => {
      const file = path.join(
        framesDir,
        `frame_${String(idx).padStart(4, "0")}.png`
      );
      await page.screenshot({ path: file });
      idx++;
    };

    // Phase 1 — Hero pause
    for (let i = 0; i < FPS * HERO_PAUSE; i++) {
      await snap();
      await sleep(1000 / FPS);
    }

    // Phase 2 — Smooth-scroll down
    const downFrames = FPS * SCROLL_DOWN;
    for (let i = 0; i < downFrames; i++) {
      const y = (scrollDistance * (i + 1)) / downFrames;
      await page.evaluate((py) => window.scrollTo(0, py), y);
      await sleep(50);
      await snap();
    }

    // Phase 3 — Bottom pause
    for (let i = 0; i < FPS * BOTTOM_PAUSE; i++) {
      await snap();
      await sleep(1000 / FPS);
    }

    // Phase 4 — Scroll back up
    const upFrames = FPS * SCROLL_UP;
    for (let i = 0; i < upFrames; i++) {
      const y = scrollDistance * (1 - (i + 1) / upFrames);
      await page.evaluate((py) => window.scrollTo(0, py), y);
      await sleep(50);
      await snap();
    }

    // ── Assemble GIF with ffmpeg (two-pass palette for quality) ──────────
    const gifPath = path.join(outputDir, `${name}.gif`);
    const palettePath = path.join(framesDir, "palette.png");
    const inputPattern = path.join(framesDir, "frame_%04d.png");

    // Pass 1 — Generate palette
    execSync(
      `ffmpeg -y -framerate ${FPS} -i "${inputPattern}" ` +
        `-vf "fps=${FPS},scale=640:-1:flags=lanczos,palettegen=stats_mode=diff" ` +
        `"${palettePath}"`,
      { stdio: "pipe" }
    );

    // Pass 2 — Create GIF using palette
    execSync(
      `ffmpeg -y -framerate ${FPS} -i "${inputPattern}" -i "${palettePath}" ` +
        `-lavfi "fps=${FPS},scale=640:-1:flags=lanczos[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle" ` +
        `"${gifPath}"`,
      { stdio: "pipe" }
    );

    // Shrink if over 5 MB
    if (fs.statSync(gifPath).size > MAX_GIF_SIZE) {
      const tmpGif = gifPath + ".tmp.gif";
      fs.renameSync(gifPath, tmpGif);
      execSync(
        `ffmpeg -y -i "${tmpGif}" -vf "fps=${Math.max(FPS - 2, 4)},scale=480:-1:flags=lanczos" "${gifPath}"`,
        { stdio: "pipe" }
      );
      fs.unlinkSync(tmpGif);
    }

    const sizeKB = (fs.statSync(gifPath).size / 1024).toFixed(0);
    console.log(`  ✅ ${name} → ${sizeKB} KB`);

    // Clean up frame images
    fs.rmSync(framesDir, { recursive: true, force: true });
    return true;
  } catch (err) {
    console.error(`  ❌ ${name}: ${err.message}`);
    fs.rmSync(framesDir, { recursive: true, force: true });
    return false;
  } finally {
    await page.close();
  }
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------
async function main() {
  const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf-8"));
  const projects = manifest.projects || {};

  // Only capture deployed or already-live sites that have a URL
  const targets = Object.entries(projects)
    .filter(
      ([, p]) =>
        ["deployed", "already-live"].includes(p.status) && p.deploy_url
    )
    .map(([n, p]) => ({ name: n, url: p.deploy_url }));

  console.log(`\n📸 Capturing ${targets.length} sites...\n`);

  if (targets.length === 0) {
    console.log("No deployed sites to capture.");
    return;
  }

  fs.mkdirSync(outputDir, { recursive: true });

  const browser = await puppeteer.launch({
    headless: "new",
    args: [
      "--no-sandbox",
      "--disable-setuid-sandbox",
      "--disable-dev-shm-usage",
    ],
  });

  let captured = 0;
  let failed = 0;

  for (const { name, url } of targets) {
    const gifPath = path.join(outputDir, `${name}.gif`);
    if (fs.existsSync(gifPath)) {
      console.log(`  ⏭  ${name} — GIF already exists`);
      captured++;
      continue;
    }
    console.log(`  🎬 ${name} (${url})...`);
    const ok = await captureSite(browser, url, name);
    ok ? captured++ : failed++;
  }

  await browser.close();
  console.log(`\n📸 Done: ${captured} captured, ${failed} failed\n`);
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

main().catch((err) => {
  console.error("Fatal:", err);
  process.exit(1);
});
