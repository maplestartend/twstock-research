import Link from "next/link";
import { ScoreBadge } from "./ScoreBadge";
import { PriceCell } from "./PriceCell";
import type { RadarHit } from "@/lib/api";

export function RadarHitChip({ hit }: { hit: RadarHit }) {
  return (
    <Link
      href={`/stocks/${hit.stockId}`}
      className="group flex items-start gap-3 p-3 rounded-lg bg-surface border-l-[3px] border-l-[var(--brand-500)] border-y border-r border-[var(--border-default)] hover:shadow-sm hover:translate-x-[1px] transition-all"
    >
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-1">
          <span className="numeric text-sm font-semibold text-[var(--text-primary)]">{hit.stockId}</span>
          <span className="text-sm text-[var(--text-secondary)] truncate">{hit.stockName}</span>
        </div>
        {hit.strategies && (
          <div className="text-xs text-[var(--text-tertiary)] leading-snug line-clamp-2">
            {hit.strategies}
          </div>
        )}
      </div>
      <div className="flex flex-col items-end gap-1 shrink-0">
        <ScoreBadge score={hit.composite} size="sm" horizon="composite" />
        {hit.close != null && (
          <PriceCell price={hit.close} variant="compact" />
        )}
      </div>
    </Link>
  );
}
