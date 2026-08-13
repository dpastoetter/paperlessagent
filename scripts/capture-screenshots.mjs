/**
 * Capture README/deck screenshots with mockup mode enabled.
 *
 * Prerequisites:
 *   uvicorn app.main:app --host 127.0.0.1 --port 8080
 *   npx playwright install chromium   # once
 *
 * Usage:
 *   node scripts/capture-screenshots.mjs
 */
import { chromium } from "playwright";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "..");
const OUT = process.env.PAPERLESS_SHOT_OUT || path.join(ROOT, "docs", "screenshots");
const BASE = process.env.PAPERLESS_SHOT_BASE || "http://127.0.0.1:8080";
const VIEWS = ["inbox", "review", "archive", "ask", "settings"];

// High-res for deck: 1920×1200 @ 2× DPR → 3840×2400 bitmaps
const WIDTH = 1920;
const HEIGHT = 1200;
const DPR = 2;

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({
  viewport: { width: WIDTH, height: HEIGHT },
  deviceScaleFactor: DPR,
  colorScheme: "dark",
});
const page = await context.newPage();

async function settle() {
  await page.waitForLoadState("networkidle").catch(() => {});
  await page.waitForTimeout(800);
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(200);
}

for (const view of VIEWS) {
  const url = `${BASE}/?mock=1&theme=slate#/${view}`;
  console.log("capturing", view, url);
  await page.goto(url, { waitUntil: "domcontentloaded" });
  await settle();
  await page.waitForFunction(() => window.PA_MOCK?.enabled === true);
  if (view === "inbox") {
    await page
      .waitForSelector(".workflow, #workflow, [class*='workflow']", { timeout: 5000 })
      .catch(() => {});
  }
  if (view === "ask") {
    await page.waitForSelector("#question", { timeout: 5000 }).catch(() => {});
  }
  const dest = path.join(OUT, `${view}.png`);
  await page.screenshot({ path: dest, type: "png", fullPage: false });
  const stat = fs.statSync(dest);
  console.log("wrote", dest, `${(stat.size / 1024).toFixed(0)} KiB`);
}

await browser.close();
console.log("done");
