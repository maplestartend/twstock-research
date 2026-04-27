// One-off：個股詳情「收盤 / 即時 / 假設」三模式 visual smoke test。
// 為 ui/typography-overhaul 分支上新增的 StockScorePanel 拍驗收圖。
// 跑完請刪除（CLAUDE.md mine #3：禁止 sprint-tag 一次性腳本累積）。
//
// 用法：node scripts/screenshot-score-panel.mjs
import { chromium } from "playwright";
import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";

const STOCK = "2330";
const URL = `http://localhost:3000/stocks/${STOCK}`;
const OUT = resolve("scripts/screenshots-score-panel");
await mkdir(OUT, { recursive: true });

const browser = await chromium.launch();
const errors = [];
try {
  const ctx = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 1,
  });
  const page = await ctx.newPage();
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(`console: ${m.text()}`);
  });

  await page.goto(URL, { waitUntil: "networkidle", timeout: 30_000 });
  await page.waitForTimeout(1500);

  // ============== 1) 收盤模式 baseline ==============
  // 確認三模式按鈕都在
  const modeBar = page.locator('button[aria-pressed]').first();
  await modeBar.waitFor({ state: "visible", timeout: 5000 });
  const closeBtn = page.getByRole("button", { name: /收盤/ });
  const liveBtn = page.getByRole("button", { name: /即時/ });
  const whatIfBtn = page.getByRole("button", { name: /假設/ });
  await closeBtn.waitFor();
  await liveBtn.waitFor();
  await whatIfBtn.waitFor();

  // baseline 收盤模式應該是 active（aria-pressed=true）
  const closePressed = await closeBtn.getAttribute("aria-pressed");
  if (closePressed !== "true") errors.push(`收盤按鈕初始未 active：${closePressed}`);

  await page.screenshot({ path: `${OUT}/01-close-mode.png`, fullPage: true });
  console.log("[ok] 收盤模式 baseline 截圖完成");

  // ============== 2) 即時模式（盤後通常 fallback prev_close 或 422） ==============
  await liveBtn.click();
  await page.waitForTimeout(2000); // 等 fetch /intraday + /score?live=1
  await page.screenshot({ path: `${OUT}/02-live-mode.png`, fullPage: true });
  console.log("[ok] 即時模式截圖完成（注：盤後可能 fallback 收盤分數）");

  // ============== 3) 假設模式 + 滑桿拖動 ==============
  await whatIfBtn.click();
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${OUT}/03-whatif-default.png`, fullPage: true });
  console.log("[ok] 假設模式（預設＝收盤價）截圖完成");

  // 拖滑桿到 -10%
  const slider = page.locator('input[type="range"][aria-label="假設成交價"]');
  await slider.waitFor();
  const min = await slider.getAttribute("min");
  await slider.fill(min ?? "0");
  await page.waitForTimeout(800); // debounce 350ms + fetch
  await page.screenshot({ path: `${OUT}/04-whatif-min.png`, fullPage: true });
  console.log("[ok] 假設模式 -10% 截圖完成（短/中分數應該變動，長期分數應不動）");

  // 拖滑桿到 +10%
  const max = await slider.getAttribute("max");
  await slider.fill(max ?? "0");
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${OUT}/05-whatif-max.png`, fullPage: true });
  console.log("[ok] 假設模式 +10% 截圖完成");

  // ============== 4) Dark mode 收盤檢查（一張就好，確保 token 沒被忽略） ==============
  await closeBtn.click();
  await page.waitForTimeout(400);
  await page.evaluate(() => {
    document.documentElement.setAttribute("data-theme", "dark");
    localStorage.setItem("theme", "dark");
  });
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${OUT}/06-close-dark.png`, fullPage: true });
  console.log("[ok] dark mode 截圖完成");
} finally {
  await browser.close();
}

if (errors.length) {
  console.error("\n[FAIL] 偵測到錯誤：");
  for (const e of errors) console.error("  -", e);
  process.exit(1);
}
console.log("\n[DONE] 所有模式截圖完成，看 scripts/screenshots-score-panel/");
