/**
 * 共用表單樣式 class 字串：取代各回測頁本地定義的 inputCls / rangeCls / btnPrimary / btnSecondary。
 * 所有頁面用同一個基準，避免漂移。
 */

// 輸入框 / select：focus ring 走全域 :focus-visible（box-shadow），這裡只負責顏色與外觀。
// hover 時 border 改 brand-300，給「可互動」的視覺回饋。
export const inputCls =
  "px-3 py-2 rounded-md border border-[var(--border-default)] bg-surface text-sm text-[var(--text-primary)] placeholder:text-[var(--text-tertiary)] hover:border-[var(--brand-300)] transition-colors";

export const rangeCls = "w-full accent-[var(--brand-500)]";

export const btnPrimary =
  "inline-flex items-center gap-1.5 px-4 h-10 rounded-md bg-[var(--brand-500)] text-white text-sm font-medium hover:bg-[var(--brand-600)] disabled:opacity-50 disabled:cursor-not-allowed transition-colors";

export const btnSecondary =
  "inline-flex items-center gap-1.5 px-3 h-10 rounded-md border border-[var(--border-default)] bg-surface text-sm font-medium text-[var(--text-secondary)] hover:bg-subtle hover:text-[var(--text-primary)] disabled:opacity-50 disabled:cursor-not-allowed transition-colors";

/** 危險操作（刪除/批次移除等）：紅底，帶懸停反白。 */
export const btnDestructive =
  "inline-flex items-center gap-1.5 px-3 h-9 rounded-md bg-[var(--color-up-bg)] text-[var(--color-up)] border border-[var(--color-up-border)] text-sm font-medium hover:bg-[var(--color-up)] hover:text-white disabled:opacity-50 disabled:cursor-not-allowed transition-colors";
