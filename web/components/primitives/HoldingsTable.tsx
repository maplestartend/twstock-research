import type { HoldingRow } from "@/lib/api";
import { fmtMoney, fmtPrice, fmtPct, toneClass } from "@/lib/format";
import { ScoreBadge } from "./ScoreBadge";
import { Th, Td } from "./Table";
import { StockIdCell } from "./StockIdCell";
import { TableContainer } from "./TableContainer";
import { cn } from "@/lib/utils";

/** 停損距離 → 三段顏色：<3% 紅（已破或快破）、3-8% 黃（接近）、>8% 綠（安全） */
function stopBucket(distPct: number | null, below: boolean): {
  cls: string;
  label: string;
} {
  if (distPct == null) return { cls: "text-[var(--text-tertiary)]", label: "—" };
  if (below || distPct < 0) {
    return {
      cls: "text-[var(--color-down)] bg-[var(--color-down-bg)] border-[var(--color-down-border)]",
      label: `已破 ${(distPct * 100).toFixed(1)}%`,
    };
  }
  if (distPct < 0.03) {
    return {
      cls: "text-[var(--color-down)] bg-[var(--color-down-bg)] border-[var(--color-down-border)]",
      label: `+${(distPct * 100).toFixed(1)}%`,
    };
  }
  if (distPct < 0.08) {
    return {
      cls: "text-[var(--warning-fg)] bg-[var(--warning-bg)] border-[var(--warning-border)]",
      label: `+${(distPct * 100).toFixed(1)}%`,
    };
  }
  return {
    cls: "text-[var(--color-up)] bg-[var(--color-up-bg)] border-[var(--color-up-border)]",
    label: `+${(distPct * 100).toFixed(1)}%`,
  };
}

export function HoldingsTable({ rows }: { rows: HoldingRow[] }) {
  if (rows.length === 0) {
    return (
      <div className="rounded-xl border border-[var(--border-default)] bg-surface p-8 text-center text-sm text-[var(--text-tertiary)]">
        目前沒有持股。到「我的持股」頁記錄交易。
      </div>
    );
  }
  return (
    <TableContainer>
      <table className="w-full text-[15px] min-w-[1100px] table-fixed">
        <thead className="bg-subtle">
          <tr>
            <Th className="w-[170px]">代號 / 名稱</Th>
            <Th align="right" className="w-[80px]">張數</Th>
            <Th align="right" className="w-[88px]">均價</Th>
            <Th align="right" className="w-[88px]">現價</Th>
            <Th align="right" className="w-[80px]">今日%</Th>
            <Th align="right" className="w-[108px]">市值</Th>
            <Th align="right" className="w-[140px]">未實現損益</Th>
            <Th align="right" className="w-[128px]">
              <span title="2×ATR 動態停損。有進場日 → 進場後高點 − 2×ATR；無進場日 → 均價 − 2×ATR">
                ATR 停損 ⓘ
              </span>
            </Th>
            <Th align="center">短 / 中 / 長</Th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const bucket = stopBucket(r.atrDistancePct, r.atrBelowStop);
            return (
            <tr key={r.stockId} className="border-t border-[var(--border-default)] hover:bg-subtle transition-colors">
              <Td>
                <StockIdCell stockId={r.stockId} stockName={r.stockName} />
              </Td>
              <Td align="right" numeric>{(r.shares / 1000).toFixed(1)}</Td>
              <Td align="right" numeric>{fmtPrice(r.avgCost)}</Td>
              <Td align="right" numeric>{fmtPrice(r.price)}</Td>
              <Td align="right" numeric>
                <span className={toneClass(r.todayPct)}>{fmtPct(r.todayPct, 2)}</span>
              </Td>
              <Td align="right" numeric>{fmtMoney(r.marketValue, 0)}</Td>
              <Td align="right" numeric>
                <div className="flex flex-col items-end gap-0.5">
                  <span className={cn("font-semibold", toneClass(r.unrealizedPnl))}>
                    {fmtMoney(r.unrealizedPnl, 0)}
                  </span>
                  <span className={cn("text-xs", toneClass(r.unrealizedPnlPct))}>
                    {fmtPct(r.unrealizedPnlPct, 2)}
                  </span>
                </div>
              </Td>
              <Td align="right" numeric>
                {r.atrStop == null ? (
                  <span className="text-[var(--text-tertiary)]">—</span>
                ) : (
                  <div className="flex flex-col items-end gap-0.5">
                    <span className="font-semibold text-[var(--text-primary)]">
                      {fmtPrice(r.atrStop)}
                    </span>
                    <span
                      className={cn(
                        "inline-flex items-center px-1.5 py-0.5 rounded border text-[11px] font-medium",
                        bucket.cls,
                      )}
                      title={`${r.atrKind === "trailing" ? "追蹤式" : "固定式"} ATR 停損`}
                    >
                      {bucket.label}
                    </span>
                  </div>
                )}
              </Td>
              <Td align="center">
                <div className="flex gap-1 justify-center">
                  <ScoreBadge score={r.shortScore} size="sm" horizon="short" />
                  <ScoreBadge score={r.midScore} size="sm" horizon="mid" />
                  <ScoreBadge score={r.longScore} size="sm" horizon="long" />
                </div>
              </Td>
            </tr>
          );
          })}
        </tbody>
      </table>
    </TableContainer>
  );
}
