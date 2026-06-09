"use client";

/**
 * 雷達命中表 + 「收盤 / 即時」切換（client island）。
 *
 * - 收盤（預設）：顯示 SSR 帶進來的 signal_history 收盤快照，不打任何即時請求。
 * - 即時：輪詢 /api/radar/hits/live（trading-hour 30s / off-hour 120s / 切到背景暫停），
 *   後端對「當前這一頁」抓盤中即時價、用同一份 score_all 重算短/中/綜合分數並於頁內重排。
 *   每列若吃到即時價（isLive）就在「收盤」欄顯示即時價 + 漲跌幅（紅漲綠跌）。
 *
 * 範圍刻意限定「當前頁」：成本有界（一頁 ~1s vs 全市場 20-30s）、對非官方 mis 端點也只打
 * 一個批次請求。長期分數結構性、不隨盤中價變動（與個股詳情頁一致）。
 */
import { useState } from "react";
import { ScoreBadge } from "@/components/primitives/ScoreBadge";
import { RecommendationTag } from "@/components/primitives/RecommendationTag";
import { PriceCell } from "@/components/primitives/PriceCell";
import { Th, Td } from "@/components/primitives/Table";
import { StockIdCell } from "@/components/primitives/StockIdCell";
import { TableContainer } from "@/components/primitives/TableContainer";
import { Icon } from "@/components/primitives/Icon";
import { useLiveListData } from "@/lib/hooks/useIntraday";
import type { RadarHit, RadarHitsPage } from "@/lib/api";
import { cn } from "@/lib/utils";

type Props = {
  initialRows: RadarHit[];
  total: number;
  liveUrl: string;
  showLongCol: boolean;
  showVrMacdCol: boolean;
  activeStrategy: string;
};

export function RadarHitsLive({
  initialRows,
  total,
  liveUrl,
  showLongCol,
  showVrMacdCol,
  activeStrategy,
}: Props) {
  const [liveMode, setLiveMode] = useState(false);
  const { data } = useLiveListData<RadarHitsPage>(liveUrl, { rows: initialRows, total }, liveMode);
  const rows = data.rows;
  const anyLive = liveMode && rows.some((r) => r.isLive);

  return (
    <div className="flex flex-col gap-3">
      {/* 收盤 / 即時 切換 */}
      <div className="flex items-center gap-3 flex-wrap">
        <div
          className="inline-flex rounded-lg border border-[var(--border-default)] overflow-hidden text-sm"
          role="group"
          aria-label="分數模式"
        >
          <button
            type="button"
            onClick={() => setLiveMode(false)}
            aria-pressed={!liveMode}
            className={cn(
              "px-3 py-1.5 inline-flex items-center gap-1.5 transition-colors",
              !liveMode
                ? "bg-[var(--brand-500)] text-white"
                : "bg-surface text-[var(--text-secondary)] hover:text-[var(--text-primary)]",
            )}
          >
            <Icon name="event_available" size={16} />收盤
          </button>
          <button
            type="button"
            onClick={() => setLiveMode(true)}
            aria-pressed={liveMode}
            className={cn(
              "px-3 py-1.5 inline-flex items-center gap-1.5 transition-colors border-l border-[var(--border-default)]",
              liveMode
                ? "bg-[var(--brand-500)] text-white"
                : "bg-surface text-[var(--text-secondary)] hover:text-[var(--text-primary)]",
            )}
          >
            <Icon name="bolt" size={16} />即時
          </button>
        </div>
        {liveMode && (
          <span className="text-[11px] text-[var(--text-tertiary)] inline-flex items-center gap-1.5">
            <span
              className={cn(
                "inline-block w-1.5 h-1.5 rounded-full",
                anyLive ? "bg-[var(--color-up)] animate-pulse" : "bg-[var(--text-tertiary)]",
              )}
            />
            盤中即時重算當前頁（每 30 秒）· 來源 TWSE mis · 僅短/中/綜合隨價變動，長期結構不變
            {liveMode && !anyLive && "·目前非盤中／抓不到即時價，暫顯示收盤"}
          </span>
        )}
      </div>

      <TableContainer>
        <table className="w-full text-[15px] min-w-[1060px] table-fixed">
          <thead className="bg-subtle">
            <tr>
              <Th sticky className="w-[170px]">代號 / 名稱</Th>
              <Th align="center" className="w-[80px]">市場</Th>
              <Th align="right" className="w-[112px]">{liveMode ? "即時 / 收盤" : "收盤"}</Th>
              <Th align="center" className="w-[88px]">短期</Th>
              <Th align="center" className="w-[88px]">中期</Th>
              {showLongCol && <Th align="center" className="w-[88px]">長期</Th>}
              <Th align="center" className="w-[88px]">綜合</Th>
              {showVrMacdCol && (
                <Th align="center" className="w-[96px]">
                  <span title="VR(26) 量能分數">VR</span>
                </Th>
              )}
              <Th align="center" className="w-[108px]">建議</Th>
              <Th>命中策略</Th>
            </tr>
          </thead>
          <tbody>
            {rows.map((h) => (
              <tr
                key={h.stockId}
                className="tv-row group border-t border-[var(--border-default)] hover:bg-subtle transition-colors"
              >
                <Td sticky>
                  <div className="inline-flex items-center gap-1.5">
                    <StockIdCell stockId={h.stockId} stockName={h.stockName} />
                    {liveMode && h.isLive && (
                      <span
                        className="inline-flex items-center text-[9px] font-medium px-1 rounded bg-[var(--color-up)]/10 text-[var(--color-up)]"
                        title="此列分數以盤中即時價重算"
                      >
                        即時
                      </span>
                    )}
                  </div>
                </Td>
                <Td align="center">
                  <span className="text-xs text-[var(--text-secondary)]">{h.market ?? "—"}</span>
                </Td>
                <Td align="right">
                  <PriceCell
                    price={h.close}
                    deltaPct={liveMode && h.isLive ? h.changePct : undefined}
                    variant="compact"
                  />
                </Td>
                <Td align="center"><div className="flex justify-center"><ScoreBadge score={h.short} size="sm" horizon="short" /></div></Td>
                <Td align="center"><div className="flex justify-center"><ScoreBadge score={h.mid} size="sm" horizon="mid" /></div></Td>
                {showLongCol && (
                  <Td align="center"><div className="flex justify-center"><ScoreBadge score={h.long} size="sm" horizon="long" /></div></Td>
                )}
                <Td align="center"><div className="flex justify-center"><ScoreBadge score={h.composite} size="sm" horizon="composite" /></div></Td>
                {showVrMacdCol && (
                  <Td align="center"><div className="flex justify-center"><ScoreBadge score={h.vrMacd} size="sm" horizon="short" /></div></Td>
                )}
                <Td align="center">
                  {h.recommendation ? (
                    <div className="flex justify-center"><RecommendationTag raw={h.recommendation} size="sm" /></div>
                  ) : (
                    <span className="text-[var(--text-tertiary)]">—</span>
                  )}
                </Td>
                <Td>
                  <div className="flex flex-wrap gap-1">
                    {(h.strategies ?? "").split(",").filter(Boolean).map((s) => (
                      <span
                        key={s}
                        className={cn(
                          "text-[11px] font-medium px-1.5 py-0.5 rounded border",
                          s.trim() === activeStrategy
                            ? "bg-[var(--brand-tint-strong)] text-[var(--brand-700)] dark:text-[var(--brand-300)] border-[var(--brand-tint-border)]"
                            : "bg-subtle text-[var(--text-secondary)] border-[var(--border-default)]",
                        )}
                      >
                        {s.trim()}
                      </span>
                    ))}
                  </div>
                </Td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableContainer>
    </div>
  );
}
