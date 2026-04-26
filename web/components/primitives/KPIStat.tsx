import { cn } from "@/lib/utils";
import { fmtPct, toneIcon, toneLabel } from "@/lib/format";
import { Icon } from "./Icon";
import { InfoTip } from "./InfoTip";
import type { TermKey } from "@/lib/terms";

type Tone = "up" | "down" | "flat" | "neutral";

export type KPIStatProps = {
  label: string;
  value: string;           // pre-formatted
  delta?: number | null;    // 小數 (0.0123 = 1.23%)
  deltaPct?: number | null; // 小數
  deltaText?: string;       // override delta 文字（例如 "延遲 2 天"）
  tone?: Tone;
  footnote?: string;
  size?: "sm" | "md" | "lg";
  /** 若提供，label 後會出現 InfoTip 用詞彙表解釋 */
  term?: TermKey;
};

const TONE_FG: Record<Tone, string> = {
  up:      "text-[var(--color-up)]",
  down:    "text-[var(--color-down)]",
  flat:    "text-[var(--color-flat)]",
  neutral: "text-[var(--text-secondary)]",
};

export function KPIStat({
  label,
  value,
  delta,
  deltaPct,
  deltaText,
  tone = "neutral",
  footnote,
  size = "md",
  term,
}: KPIStatProps) {
  const valueSize =
    size === "lg" ? "text-[36px] leading-[40px]" :
    size === "sm" ? "text-[22px] leading-[28px]" :
                    "text-[28px] leading-[32px]";

  return (
    <div
      role="group"
      aria-label={`${label} ${value}`}
      className="rounded-xl border border-[var(--border-default)] bg-surface p-5 flex flex-col gap-2 transition-all hover:border-[var(--border-strong)] hover:shadow-sm"
    >
      <div className="text-sm font-medium text-[var(--text-tertiary)] inline-flex items-center gap-1">
        {label}
        {term && <InfoTip term={term} />}
      </div>
      <div className={cn("numeric font-bold text-[var(--text-primary)] tracking-tight", valueSize)}>
        {value}
      </div>
      {(delta != null || deltaPct != null || deltaText) && (
        <div className={cn("numeric text-sm font-medium flex items-center gap-1.5", TONE_FG[tone])}>
          {deltaText ? (
            <span>{deltaText}</span>
          ) : (
            <>
              {deltaPct != null && (
                <span className="inline-flex items-center">
                  <Icon name={toneIcon(deltaPct)} size={18} label={toneLabel(deltaPct)} />
                  {fmtPct(deltaPct, 2)}
                </span>
              )}
              {delta != null && deltaPct != null && (
                <span className="text-[var(--text-tertiary)]">·</span>
              )}
              {delta != null && (
                <span>{delta > 0 ? "+" : delta < 0 ? "−" : ""}{Math.abs(delta).toLocaleString("zh-TW", { maximumFractionDigits: 2 })}</span>
              )}
            </>
          )}
        </div>
      )}
      {footnote && (
        <div className="text-xs text-[var(--text-tertiary)] mt-0.5">{footnote}</div>
      )}
    </div>
  );
}
