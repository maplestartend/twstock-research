/**
 * 評分子項目的中文標籤對照表 (PART_LABEL)。
 *
 * 兩處消費者：
 * 1. weight-tuner/client.tsx：完整子項清單（短期 9 + 中期 5 + 長期 5 = 19）
 * 2. ScoreBreakdownBars.tsx：個股詳情的子項目分數條
 *
 * Single source of truth：與 terms.ts 重疊的 18 個 key 直接從 TERMS 衍生 label，
 * 避免「同一個鍵維護兩份中文翻譯」的 drift 風險。只在 terms.ts 沒有定義
 * （但 scoring engine 仍可能輸出）的 10 個 key 才在這邊額外列。
 */
import { TERMS } from "./terms";

// 從 terms.ts 自動抽 label 欄位，省掉手寫 18 個 key 的對照
const TERMS_LABELS = Object.fromEntries(
  Object.entries(TERMS).map(([k, v]) => [k, v.label]),
) as Record<string, string>;

// scoring engine 可能輸出但 terms.ts 沒覆蓋的舊 key（保留 fallback 翻譯）
const EXTRA_LABELS: Record<string, string> = {
  breakout: "突破",
  pullback: "回檔",
  strength: "相對強弱",
  chip: "籌碼",
  margin: "融資",
  revenue: "營收",
  revenue_yoy: "營收 YoY",
  eps: "EPS",
  per: "PER",
  value: "估值",
};

export const PART_LABEL: Record<string, string> = {
  ...TERMS_LABELS,
  ...EXTRA_LABELS,
};
