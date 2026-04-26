"use client";

import { useState, useRef, useEffect } from "react";
import { Icon } from "./Icon";
import { cn } from "@/lib/utils";
import { getTerm, type TermKey } from "@/lib/terms";

/**
 * 名詞解釋小圖示。hover / 點擊顯示 tooltip。
 *
 * 用法：
 *   <InfoTip term="slippage" />                       // 純 icon，使用詞彙表
 *   <InfoTip term="slippage" inline />                // 顯示「滑價 ⓘ」 inline
 *   <InfoTip text="自訂的解釋文字" />                  // 不走詞彙表
 *
 * 設計原則：
 * - 預設 desktop hover 顯示、mobile 點擊顯示
 * - tooltip 用絕對定位、最大寬 280px、自動換行
 * - 不依賴第三方 lib（保持 bundle 小）
 */
export function InfoTip({
  term,
  text,
  inline = false,
  className,
}: {
  term?: TermKey;
  text?: string;
  inline?: boolean;
  className?: string;
}) {
  const def = term ? getTerm(term) : null;
  const tooltip = text ?? def?.long ?? "";
  const label = def?.label ?? "";
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("click", onClick);
    return () => document.removeEventListener("click", onClick);
  }, [open]);

  if (!tooltip) return null;

  return (
    <span
      ref={ref}
      className={cn(
        "relative inline-flex items-center gap-1 align-middle",
        className,
      )}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      {inline && label && <span>{label}</span>}
      <button
        type="button"
        aria-label={`什麼是「${label || "說明"}」？`}
        onClick={(e) => {
          e.preventDefault();
          e.stopPropagation();
          setOpen((v) => !v);
        }}
        className="inline-flex items-center justify-center w-4 h-4 rounded-full text-[var(--text-tertiary)] hover:text-[var(--brand-500)] focus:outline-none focus-visible:ring-1 focus-visible:ring-[var(--brand-500)]"
      >
        <Icon name="info" size={14} />
      </button>

      {open && (
        <span
          role="tooltip"
          className="absolute z-50 top-full left-0 mt-1.5 w-[280px] max-w-[calc(100vw-2rem)] px-3 py-2 rounded-lg bg-[var(--tooltip-bg)] text-[var(--tooltip-fg)] border border-[var(--tooltip-border)] text-xs leading-relaxed shadow-lg pointer-events-none"
        >
          {label && <span className="font-semibold block mb-0.5">{label}</span>}
          {tooltip}
        </span>
      )}
    </span>
  );
}
