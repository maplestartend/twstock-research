// Full-page screenshots of every main page for multi-agent visual review.
// Usage: node scripts/screenshot-all.mjs
import { chromium } from "playwright";
import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";

const OUT = resolve("scripts/screenshots-all");
await mkdir(OUT, { recursive: true });

const setTheme = async (page, theme) => {
  await page.evaluate((t) => {
    document.documentElement.setAttribute("data-theme", t);
    localStorage.setItem("theme", t);
  }, theme);
  await page.waitForTimeout(200);
};

// Pages to capture. `wait` is the selector / state we need before snapping.
// `slow` means we should wait longer for charts/heavy data.
const PAGES = [
  { slug: "home", path: "/", slow: true },
  { slug: "sectors", path: "/sectors", slow: true },
  { slug: "watchlist", path: "/watchlist", slow: false },
  { slug: "stocks-2330", path: "/stocks/2330", slow: true },
  { slug: "holdings", path: "/holdings", slow: false },
  { slug: "radar", path: "/radar", slow: true },
  { slug: "dividend-calendar", path: "/dividend-calendar", slow: false },
  { slug: "alerts", path: "/alerts", slow: false },
  { slug: "history", path: "/history", slow: true },
  { slug: "journal", path: "/journal", slow: false },
  { slug: "lab", path: "/lab", slow: false },
  { slug: "watchlist-manage", path: "/watchlist-manage", slow: false },
  { slug: "backtest", path: "/backtest", slow: false },
  { slug: "portfolio-backtest", path: "/portfolio-backtest", slow: false },
  { slug: "event-backtest", path: "/event-backtest", slow: false },
  { slug: "grid-search", path: "/grid-search", slow: false },
  { slug: "weight-tuner", path: "/weight-tuner", slow: false },
  { slug: "dq", path: "/dq", slow: true },
  { slug: "diagnostics", path: "/diagnostics", slow: false },
];

const THEMES = ["dark", "light"];

const browser = await chromium.launch();
const errors = [];
try {
  const ctx = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 1,
  });
  const page = await ctx.newPage();

  page.on("pageerror", (err) => {
    errors.push({ page: page.url(), msg: err.message });
    console.error("[pageerror]", page.url(), "—", err.message);
  });
  page.on("console", (msg) => {
    if (msg.type() === "error") {
      errors.push({ page: page.url(), msg: msg.text() });
      console.log(`[console.error]`, page.url(), "—", msg.text());
    }
  });

  for (const p of PAGES) {
    for (const theme of THEMES) {
      const url = `http://localhost:3000${p.path}`;
      try {
        await page.goto(url, { waitUntil: "networkidle", timeout: 30_000 });
        await setTheme(page, theme);
        await page.waitForTimeout(p.slow ? 1500 : 600);
        // scroll to bottom to trigger any lazy components, then back to top
        await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
        await page.waitForTimeout(300);
        await page.evaluate(() => window.scrollTo(0, 0));
        await page.waitForTimeout(200);
        const file = `${OUT}/${p.slug}-${theme}.png`;
        await page.screenshot({ path: file, fullPage: true });
        console.log(`[ok] ${p.slug} ${theme}`);
      } catch (e) {
        console.error(`[fail] ${p.slug} ${theme}:`, e.message);
        errors.push({ page: url, msg: `screenshot failed: ${e.message}` });
      }
    }
  }

  // Mobile viewport — only home + sectors + stocks detail (most layout-sensitive)
  const mobileCtx = await browser.newContext({
    viewport: { width: 390, height: 844 }, // iPhone 14 Pro
    deviceScaleFactor: 2,
    isMobile: true,
    hasTouch: true,
  });
  const mPage = await mobileCtx.newPage();
  for (const slug of ["home", "sectors", "stocks-2330", "radar", "holdings"]) {
    const path = PAGES.find((p) => p.slug === slug).path;
    try {
      await mPage.goto(`http://localhost:3000${path}`, {
        waitUntil: "networkidle",
        timeout: 30_000,
      });
      await setTheme(mPage, "dark");
      await mPage.waitForTimeout(1200);
      await mPage.screenshot({ path: `${OUT}/${slug}-mobile.png`, fullPage: true });
      console.log(`[ok] ${slug} mobile`);
    } catch (e) {
      console.error(`[fail] ${slug} mobile:`, e.message);
    }
  }
} finally {
  await browser.close();
}

console.log(`\n[done] errors collected: ${errors.length}`);
if (errors.length) {
  for (const e of errors.slice(0, 20)) console.log(`  - ${e.page}: ${e.msg}`);
}
