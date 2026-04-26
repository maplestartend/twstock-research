// zh-TW 格式化工具。所有顯示層都走這裡，禁止 toFixed(...).replace(...) 風格。

const NBSP = " ";

export function fmtNum(v: number | null | undefined, digits = 0): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return new Intl.NumberFormat("zh-TW", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(v);
}

/** 股價：最多兩位小數，去尾零。2330.00 → 2,330；8.234 → 8.23。 */
export function fmtPrice(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  let s = new Intl.NumberFormat("zh-TW", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(v);
  if (s.includes(".")) s = s.replace(/\.?0+$/, "");
  return s;
}

/** 百分比：0.0234 → "+2.34%"；0 → "0.00%"；null → "—"。 */
export function fmtPct(v: number | null | undefined, digits = 2): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const sign = v > 0 ? "+" : v < 0 ? "−" : "";
  const abs = Math.abs(v * 100);
  return `${sign}${abs.toFixed(digits)}%`;
}

/** 分數：0-100，最多一位小數，去尾零。68.0 → 68；67.57 → 67.6。 */
export function fmtScore(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  let s = v.toFixed(1);
  if (s.includes(".")) s = s.replace(/\.?0+$/, "");
  return s;
}

/** 金額：+1,234,567 / −1,234 / —；符號用數學減號 "−" 對齊 tabular-nums。 */
export function fmtMoney(v: number | null | undefined, digits = 0): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const sign = v > 0 ? "+" : v < 0 ? "−" : "";
  const abs = Math.abs(v);
  return sign + new Intl.NumberFormat("zh-TW", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(abs);
}

/** 成交值 (TWD)：依量級自動換成 億/萬。例 1.23e10 → "123 億"、1.5e7 → "1,500 萬"、3000 → "3,000"。 */
export function fmtTradeValue(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const abs = Math.abs(v);
  const sign = v < 0 ? "−" : "";
  if (abs >= 1e8) {
    const yi = abs / 1e8;
    return `${sign}${yi.toFixed(yi >= 100 ? 0 : yi >= 10 ? 1 : 2)} 億`;
  }
  if (abs >= 1e4) {
    const wan = abs / 1e4;
    return `${sign}${new Intl.NumberFormat("zh-TW", { maximumFractionDigits: 0 }).format(wan)} 萬`;
  }
  return sign + new Intl.NumberFormat("zh-TW", { maximumFractionDigits: 0 }).format(abs);
}

// ===== Taipei-timezone aware date helpers =====
// 系統預設行為 `toISOString()` 吐 UTC，在 UTC 時區（如 Vercel/部分雲端）或午夜前後會顯示錯日。
// 下面都明確指定 Asia/Taipei，確保伺服器時區無論如何都顯示正確台灣日期。
const TAIPEI_TZ = "Asia/Taipei";

function _taipeiParts(d: Date): { year: string; month: string; day: string } {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: TAIPEI_TZ,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(d);
  return {
    year:  parts.find((p) => p.type === "year")?.value  ?? "",
    month: parts.find((p) => p.type === "month")?.value ?? "",
    day:   parts.find((p) => p.type === "day")?.value   ?? "",
  };
}

/** 依台北時區取 "YYYY-MM-DD"。預設用 now。 */
export function taipeiDate(d: Date = new Date()): string {
  const p = _taipeiParts(d);
  return `${p.year}-${p.month}-${p.day}`;
}

/** 依台北時區取週幾中文單字（日/一/二/…/六）。 */
export function taipeiWeekday(d: Date = new Date()): string {
  return new Intl.DateTimeFormat("zh-TW", {
    timeZone: TAIPEI_TZ,
    weekday: "narrow",
  }).format(d);
}

export function fmtDate(d: string | Date | null | undefined): string {
  if (!d) return "—";
  const dt = typeof d === "string" ? new Date(d) : d;
  if (isNaN(dt.getTime())) return "—";
  return taipeiDate(dt);
}

/** "04/25" — 表格空間有限時用；以台北時區判日。 */
export function fmtDateShort(d: string | Date | null | undefined): string {
  if (!d) return "—";
  const dt = typeof d === "string" ? new Date(d) : d;
  if (isNaN(dt.getTime())) return "—";
  const p = _taipeiParts(dt);
  return `${p.month}/${p.day}`;
}

/** 依漲跌回傳 CSS class：up / down / flat，使用者層再組合 text-* / bg-* */
export function tone(v: number | null | undefined): "up" | "down" | "flat" {
  if (v == null || !Number.isFinite(v) || v === 0) return "flat";
  return v > 0 ? "up" : "down";
}

/** 依漲跌回傳符號：▲ / ▼ / ▬（色盲友善 fallback；僅用於 screen reader 或無 Icon 環境）*/
export function toneGlyph(v: number | null | undefined): string {
  const t = tone(v);
  if (t === "up") return "▲";
  if (t === "down") return "▼";
  return "▬";
}

/** 依漲跌回傳 Material Symbols 名稱。compact 用於 inline（下拉型箭頭），否則 trending 風格 */
export function toneIcon(v: number | null | undefined, variant: "compact" | "trending" = "compact"): string {
  const t = tone(v);
  if (variant === "trending") {
    if (t === "up") return "trending_up";
    if (t === "down") return "trending_down";
    return "trending_flat";
  }
  if (t === "up") return "arrow_drop_up";
  if (t === "down") return "arrow_drop_down";
  return "remove";
}

/** 台股漲跌文字（a11y 用）*/
export function toneLabel(v: number | null | undefined): string {
  const t = tone(v);
  if (t === "up") return "漲";
  if (t === "down") return "跌";
  return "平";
}

/** 依漲跌回傳對應 text-color CSS class。多個頁面內部都自己寫一份，統一抽出。 */
export function toneClass(v: number | null | undefined, opts?: { neutralFg?: "primary" | "secondary" | "tertiary" }): string {
  const t = tone(v);
  if (t === "up") return "text-[var(--color-up)]";
  if (t === "down") return "text-[var(--color-down)]";
  const fg = opts?.neutralFg ?? "primary";
  return `text-[var(--text-${fg})]`;
}

/** 分數 0-100 → 五段 tier。對齊現有 color_score 規則。 */
export function scoreTier(v: number | null | undefined): "strong-pos" | "pos" | "neutral" | "caution" | "danger" | "unknown" {
  if (v == null || !Number.isFinite(v)) return "unknown";
  if (v >= 70) return "strong-pos";
  if (v >= 55) return "pos";
  if (v >= 45) return "neutral";
  if (v >= 30) return "caution";
  return "danger";
}

export const _NBSP = NBSP;
