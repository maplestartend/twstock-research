import Link from "next/link";
import type { SnapshotDelta } from "@/lib/api";
import { Icon } from "./Icon";
import { cn } from "@/lib/utils";

/** 戰情室「今日 vs 昨日」面板：新進命中 / 跌出命中 / 綜合分數大幅變化。
 * PM 審查 P0-6：每日 loop 在乎變化、不是絕對值；舊 dashboard 缺這個角度。
 */
export function SnapshotDeltaPanel({ delta }: { delta: SnapshotDelta }) {
  if (!delta.prevAsOf) {
    return (
      <div className="rounded-xl border border-dashed border-[var(--border-default)] bg-surface/60 p-4 text-sm text-[var(--text-tertiary)] text-center">
        signal_history 只有 1 天歷史，明天盤後再來看「今日 vs 昨日」變化。
      </div>
    );
  }

  const empty =
    delta.newHits.length === 0 &&
    delta.droppedHits.length === 0 &&
    delta.bigMovers.length === 0;

  return (
    <div className="rounded-xl border border-[var(--border-default)] bg-surface p-4 flex flex-col gap-4">
      <div className="text-xs text-[var(--text-tertiary)] flex items-center gap-2">
        <Icon name="compare_arrows" size={14} />
        <span className="numeric">{delta.prevAsOf}</span>
        <Icon name="arrow_forward" size={12} />
        <span className="numeric">{delta.latestAsOf}</span>
      </div>

      {empty && (
        <div className="text-xs text-[var(--text-tertiary)] text-center py-4">
          昨日今日無顯著變化（無新進/跌出命中、無分數大幅波動）
        </div>
      )}

      {delta.newHits.length > 0 && (
        <DeltaSection
          icon="trending_up"
          tone="up"
          title={`新進命中 (${delta.newHits.length})`}
        >
          {delta.newHits.map((h) => (
            <li key={h.stockId} className="flex items-center gap-2 text-sm">
              <Link href={`/stocks/${h.stockId}`} className="numeric font-semibold w-12 hover:underline">
                {h.stockId}
              </Link>
              <span className="text-[var(--text-secondary)] flex-1 truncate">{h.stockName}</span>
              <span className="text-[10px] text-[var(--text-tertiary)] truncate max-w-[180px]">
                +{h.strategies.join(", ")}
              </span>
              {h.composite != null && (
                <span className="numeric text-xs text-[var(--text-secondary)] w-10 text-right">
                  {h.composite.toFixed(1)}
                </span>
              )}
            </li>
          ))}
        </DeltaSection>
      )}

      {delta.droppedHits.length > 0 && (
        <DeltaSection
          icon="trending_down"
          tone="down"
          title={`跌出命中 (${delta.droppedHits.length})`}
        >
          {delta.droppedHits.map((h) => (
            <li key={h.stockId} className="flex items-center gap-2 text-sm">
              <Link href={`/stocks/${h.stockId}`} className="numeric font-semibold w-12 hover:underline">
                {h.stockId}
              </Link>
              <span className="text-[var(--text-secondary)] flex-1 truncate">{h.stockName}</span>
              <span className="text-[10px] text-[var(--text-tertiary)] truncate max-w-[180px]">
                −{h.strategies.join(", ")}
              </span>
              {h.composite != null && (
                <span className="numeric text-xs text-[var(--text-secondary)] w-10 text-right">
                  {h.composite.toFixed(1)}
                </span>
              )}
            </li>
          ))}
        </DeltaSection>
      )}

      {delta.bigMovers.length > 0 && (
        <DeltaSection
          icon="bolt"
          tone="neutral"
          title={`分數大幅變化 (${delta.bigMovers.length})`}
        >
          {delta.bigMovers.map((m) => {
            const positive = (m.delta ?? 0) > 0;
            return (
              <li key={m.stockId} className="flex items-center gap-2 text-sm">
                <Link href={`/stocks/${m.stockId}`} className="numeric font-semibold w-12 hover:underline">
                  {m.stockId}
                </Link>
                <span className="text-[var(--text-secondary)] flex-1 truncate">{m.stockName}</span>
                <span className="numeric text-xs text-[var(--text-tertiary)]">
                  {m.prevComposite?.toFixed(1) ?? "—"} → {m.latestComposite?.toFixed(1) ?? "—"}
                </span>
                <span
                  className={cn(
                    "numeric text-xs font-semibold w-12 text-right",
                    positive ? "text-[var(--color-up)]" : "text-[var(--color-down)]",
                  )}
                >
                  {positive ? "+" : ""}{m.delta?.toFixed(1)}
                </span>
              </li>
            );
          })}
        </DeltaSection>
      )}
    </div>
  );
}

function DeltaSection({
  title,
  icon,
  tone,
  children,
}: {
  title: string;
  icon: string;
  tone: "up" | "down" | "neutral";
  children: React.ReactNode;
}) {
  const cls =
    tone === "up" ? "text-[var(--color-up)]" :
    tone === "down" ? "text-[var(--color-down)]" :
    "text-[var(--text-secondary)]";
  return (
    <div className="flex flex-col gap-1.5">
      <div className={cn("text-xs font-semibold inline-flex items-center gap-1", cls)}>
        <Icon name={icon} size={14} filled />
        {title}
      </div>
      <ul className="flex flex-col gap-1">{children}</ul>
    </div>
  );
}
