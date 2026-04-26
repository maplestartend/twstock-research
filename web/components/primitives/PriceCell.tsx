import { cn } from "@/lib/utils";
import { fmtPrice, fmtPct, tone as toneOf, toneIcon, toneLabel } from "@/lib/format";
import { Icon } from "./Icon";

export type PriceCellProps = {
  price: number | null | undefined;
  prevClose?: number | null | undefined;
  deltaPct?: number | null | undefined;   // 優先於 prevClose 計算
  variant?: "compact" | "default" | "expanded";
  align?: "left" | "right";
};

export function PriceCell({ price, prevClose, deltaPct, variant = "compact", align = "right" }: PriceCellProps) {
  const pct = deltaPct != null
    ? deltaPct
    : (price != null && prevClose != null && prevClose !== 0)
      ? (price - prevClose) / prevClose
      : null;
  const delta = price != null && prevClose != null ? price - prevClose : null;
  const t = toneOf(pct);

  if (variant === "expanded") {
    return (
      <div className={cn("flex flex-col gap-1", align === "right" ? "items-end" : "items-start")}>
        <span className={cn("numeric text-[36px] font-bold leading-none", toneClass(t))}>
          {fmtPrice(price)}
        </span>
        <span className={cn("numeric text-base font-medium inline-flex items-center gap-0.5", toneClass(t))}>
          <Icon name={toneIcon(pct)} size={22} label={toneLabel(pct)} />
          {fmtPct(pct, 2)}
          {delta != null && (
            <span className="text-[var(--text-tertiary)] ml-2">
              {delta > 0 ? "+" : delta < 0 ? "−" : ""}{Math.abs(delta).toFixed(2)}
            </span>
          )}
        </span>
      </div>
    );
  }

  if (variant === "default") {
    return (
      <div className={cn("flex flex-col gap-0.5", align === "right" ? "items-end" : "items-start")}>
        <span className={cn("numeric text-xl font-bold", toneClass(t))}>{fmtPrice(price)}</span>
        <span className={cn("numeric text-sm font-medium inline-flex items-center gap-0.5", toneClass(t))}>
          <Icon name={toneIcon(pct)} size={18} label={toneLabel(pct)} />
          {fmtPct(pct, 2)}
        </span>
      </div>
    );
  }

  // compact
  return (
    <span className={cn("numeric inline-flex items-center gap-0.5", align === "right" && "justify-end", toneClass(t))}>
      <span className="font-medium">{fmtPrice(price)}</span>
      {pct != null && (
        <span className="inline-flex items-center text-xs">
          <Icon name={toneIcon(pct)} size={16} label={toneLabel(pct)} />
          {fmtPct(pct, 2)}
        </span>
      )}
    </span>
  );
}

function toneClass(t: "up" | "down" | "flat"): string {
  if (t === "up") return "text-[var(--color-up)]";
  if (t === "down") return "text-[var(--color-down)]";
  return "text-[var(--text-primary)]";
}
