// Headless screenshots for visual verification of /sectors heatmap.
// Usage: node scripts/screenshot.mjs
import { chromium } from "playwright";
import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";

const OUT = resolve("scripts/screenshots");
await mkdir(OUT, { recursive: true });

const setTheme = async (page, theme) => {
  await page.evaluate((t) => {
    document.documentElement.setAttribute("data-theme", t);
    localStorage.setItem("theme", t);
  }, theme);
  await page.waitForTimeout(150);
};

const browser = await chromium.launch();
try {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  page.on("pageerror", (err) => console.error("[pageerror]", err.message));
  page.on("console", (msg) => {
    if (msg.type() === "error" || msg.type() === "warning") {
      console.log(`[console.${msg.type()}]`, msg.text());
    }
  });

  await page.goto("http://localhost:3000/sectors", { waitUntil: "networkidle" });
  // wait for heatmap svg to mount (ResizeObserver -> width -> render)
  await page.waitForSelector('svg[aria-label="產業熱力圖"] rect', { timeout: 10_000 });
  await page.waitForTimeout(300);

  // Heatmap section is below the ranking table.
  // svg → inner measurement div → outer rounded/bordered box (the one we want to capture).
  const heatmapBox = page.locator('svg[aria-label="產業熱力圖"]').locator("..").locator("..");
  await heatmapBox.scrollIntoViewIfNeeded();
  await page.waitForTimeout(200);

  // 1. light theme
  await setTheme(page, "light");
  await heatmapBox.screenshot({ path: `${OUT}/sectors-light.png` });

  // 2. dark theme
  await setTheme(page, "dark");
  await heatmapBox.screenshot({ path: `${OUT}/sectors-dark.png` });

  // 3. hover (dark) — hover the largest tile (first child by area = first in <svg>)
  // Hover popup is fixed-position, can extend outside heatmap box → use viewport screenshot for these.
  const firstTile = page.locator('svg[aria-label="產業熱力圖"] g[style*="cursor"]').first();
  await firstTile.hover();
  await page.waitForTimeout(200);
  await page.screenshot({ path: `${OUT}/sectors-dark-hover.png`, fullPage: false });

  // 4. light + hover too, since popup contrast may differ
  await setTheme(page, "light");
  await firstTile.hover();
  await page.waitForTimeout(200);
  await page.screenshot({ path: `${OUT}/sectors-light-hover.png`, fullPage: false });

  // 5. count rects so we can sanity-check d3 layout
  const rectCount = await page
    .locator('svg[aria-label="產業熱力圖"] rect')
    .count();
  console.log(`[ok] rendered ${rectCount} tiles`);

  // 6. dump tile bounding boxes to see if any extreme aspect ratios remain
  const bboxes = await page.$$eval('svg[aria-label="產業熱力圖"] rect', (rects) =>
    rects.map((r) => {
      const b = r.getBoundingClientRect();
      return { w: Math.round(b.width), h: Math.round(b.height) };
    }),
  );
  const ratios = bboxes
    .filter((b) => b.w > 0 && b.h > 0)
    .map((b) => Math.max(b.w, b.h) / Math.min(b.w, b.h));
  ratios.sort((a, b) => b - a);
  console.log(
    `[ok] tile aspect ratios — worst 5: ${ratios
      .slice(0, 5)
      .map((r) => r.toFixed(2))
      .join(", ")}`,
  );
} finally {
  await browser.close();
}
