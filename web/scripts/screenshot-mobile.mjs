// Verify S1-1 mobile sidebar drawer + responsive padding.
import { chromium } from "playwright";
import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";

const OUT = resolve("scripts/screenshots-mobile");
await mkdir(OUT, { recursive: true });

const setTheme = async (page, theme) => {
  await page.evaluate((t) => {
    document.documentElement.setAttribute("data-theme", t);
    localStorage.setItem("theme", t);
  }, theme);
  await page.waitForTimeout(200);
};

const browser = await chromium.launch();
try {
  // iPhone 14 Pro viewport
  const ctx = await browser.newContext({
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 2,
    isMobile: true,
    hasTouch: true,
  });
  const page = await ctx.newPage();
  page.on("pageerror", (err) => console.error("[pageerror]", err.message));

  const PAGES = [
    { slug: "home", path: "/" },
    { slug: "holdings", path: "/holdings" },
    { slug: "stocks-3260", path: "/stocks/3260" },
    { slug: "radar", path: "/radar" },
    { slug: "sectors", path: "/sectors" },
  ];

  // Warm-up pass：dev server 第一次走過每頁時編譯 chunk，CSS 可能 404；
  // 等到全部跑過一遍且穩定，第二輪截圖才會拿到完整樣式。
  for (const p of PAGES) {
    try {
      await page.goto(`http://localhost:3000${p.path}`, { waitUntil: "networkidle", timeout: 30_000 });
      await page.waitForTimeout(800);
    } catch (e) {
      console.warn(`[warm-up] ${p.slug}: ${e.message}`);
    }
  }
  await page.waitForTimeout(2000);

  for (const p of PAGES) {
    await page.goto(`http://localhost:3000${p.path}`, { waitUntil: "networkidle", timeout: 30_000 });
    await setTheme(page, "dark");
    await page.waitForTimeout(1500);
    // 確認頁面有套到 CSS（檢查 body computed background 不是預設白）
    const bodyBg = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
    if (bodyBg === "rgba(0, 0, 0, 0)" || bodyBg === "rgb(255, 255, 255)") {
      console.warn(`[warn] ${p.slug}: body bg=${bodyBg}, possibly unstyled — retrying`);
      await page.reload({ waitUntil: "networkidle" });
      await setTheme(page, "dark");
      await page.waitForTimeout(2000);
    }
    await page.screenshot({ path: `${OUT}/${p.slug}-closed.png`, fullPage: true });
    console.log(`[ok] ${p.slug} closed`);
  }

  // Open drawer on /holdings, snap viewport (not full-page) so we see the drawer overlay
  await page.goto("http://localhost:3000/holdings", { waitUntil: "networkidle" });
  await setTheme(page, "dark");
  await page.waitForTimeout(800);
  // Click the hamburger (aria-label="開啟選單")
  await page.click('button[aria-label="開啟選單"]');
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${OUT}/holdings-drawer-open.png`, fullPage: false });
  console.log(`[ok] holdings drawer-open`);

  // Backdrop click closes — 改用座標點擊（drawer 只占左半，右半空白即 backdrop）
  try {
    await page.mouse.click(350, 400);
    await page.waitForTimeout(400);
    await page.screenshot({ path: `${OUT}/holdings-drawer-closed.png`, fullPage: false });
    console.log(`[ok] holdings drawer-closed (after backdrop click)`);
  } catch (e) {
    console.warn(`[skip] backdrop click: ${e.message}`);
  }

  // Desktop check (viewport 1440) — sidebar should look unchanged
  const desktopCtx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const desk = await desktopCtx.newPage();
  await desk.goto("http://localhost:3000/holdings", { waitUntil: "networkidle" });
  await setTheme(desk, "dark");
  await desk.waitForTimeout(800);
  await desk.screenshot({ path: `${OUT}/holdings-desktop.png`, fullPage: false });
  console.log(`[ok] holdings desktop (regression check)`);
} finally {
  await browser.close();
}
