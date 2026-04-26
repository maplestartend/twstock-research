import Link from "next/link";
import {
  apiGet,
  apiPost,
  humanizeApiError,
  type EventBacktestResponse,
  type EventTradeRow,
} from "@/lib/api";
import { Icon } from "@/components/primitives/Icon";
import { PageHeader } from "@/components/primitives/PageHeader";
import { SurvivorshipWarning } from "@/components/primitives/SurvivorshipWarning";
import { EmptyState } from "@/components/primitives/EmptyState";
import { KPIStat } from "@/components/primitives/KPIStat";
import { BackendDownError } from "@/components/primitives/BackendDownError";
import { Field } from "@/components/primitives/Field";
import { Th, TdCompact as Td } from "@/components/primitives/Table";
import { TableContainer } from "@/components/primitives/TableContainer";
import { StockIdCell } from "@/components/primitives/StockIdCell";
import { fmtPct, fmtPrice, tone, toneClass as tc } from "@/lib/format";
import { btnPrimary, btnSecondary, inputCls, rangeCls } from "@/lib/formClasses";
import { EVENT_SCENARIOS as SCENARIOS } from "@/lib/scenarios";
import { cn } from "@/lib/utils";

export const revalidate = 0;

type WatchlistEntry = { stockId: string; stockName: string };

function num(v: string | undefined, fb: number): number {
  if (v == null) return fb;
  const n = Number(v);
  return Number.isFinite(n) ? n : fb;
}

export default async function EventBacktestPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const sp = await searchParams;
  const source = (sp.source ?? "watchlist") as "watchlist" | "custom";
  const customIds = (sp.tickers ?? "").trim();
  const entry = num(sp.entry, -5);
  const exit = num(sp.exit, 10);
  const sinceYear = num(sp.year, 2020);
  const minDiv = num(sp.minDiv, 0.5);
  const hasRun = !!sp.run;

  let wl: WatchlistEntry[];
  try {
    wl = await apiGet<WatchlistEntry[]>("/api/watchlist", { noCache: true });
  } catch (e) {
    return <BackendDownError error={e} pageTitle="除權息事件回測" />;
  }

  let stockIds: string[] = [];
  if (hasRun) {
    stockIds = source === "custom"
      ? customIds.split(/[\s,，]+/).map((s) => s.trim()).filter(Boolean)
      : wl.map((e) => e.stockId);
  }

  let result: EventBacktestResponse | null = null;
  let errorMsg: string | null = null;
  if (hasRun && stockIds.length > 0) {
    try {
      result = await apiPost<EventBacktestResponse>("/api/backtest/event-driven", {
        stock_ids: stockIds.slice(0, 100),
        entry_offset: entry,
        exit_offset: exit,
        since_year: sinceYear,
        min_dividend: minDiv,
      });
    } catch (e) {
      errorMsg = (e as Error).message;
    }
  }

  return (
    <div className="p-4 lg:p-8 flex flex-col gap-8 max-w-[1600px] mx-auto">
      <PageHeader
        title="除權息事件回測"
        icon="celebration"
        description="驗證「除權息套利」這類已知策略的歷史勝率：事件前 N 日進場、事件後 M 日出場，看含息報酬是否真的賺錢。"
        extra="報酬包含「價格漲跌 + 收到的現金股利」。資料源 adj_event 表（除權息實際發生時的還原因子記錄）。"
      />
      <SurvivorshipWarning />

      {/* 情景預設 */}
      <section className="flex flex-col gap-2">
        <h2 className="text-sm font-semibold text-[var(--text-secondary)] inline-flex items-center gap-1.5">
          <Icon name="palette" size={16} className="text-[var(--brand-500)]" />
          情景預設
          <span className="text-[11px] text-[var(--text-tertiary)] font-normal ml-1">（一鍵套用 + 直接執行）</span>
        </h2>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          {(Object.entries(SCENARIOS) as [keyof typeof SCENARIOS, typeof SCENARIOS[keyof typeof SCENARIOS]][]).map(([key, sc]) => {
            const isActive = entry === sc.entry && exit === sc.exit;
            const q = new URLSearchParams({
              run: "1", source,
              ...(source === "custom" && customIds ? { tickers: customIds } : {}),
              entry: String(sc.entry), exit: String(sc.exit),
              year: String(sinceYear), minDiv: String(minDiv),
            }).toString();
            return (
              <Link
                key={key}
                href={`/event-backtest?${q}`}
                className={cn(
                  "rounded-xl border p-4 flex flex-col gap-1.5 transition-colors",
                  isActive && hasRun
                    ? "border-[var(--brand-500)] bg-[var(--brand-tint)]"
                    : "border-[var(--border-default)] bg-surface hover:border-[var(--brand-300)]",
                )}
              >
                <span className="inline-flex items-center gap-1.5 text-sm font-semibold">
                  <Icon name={sc.icon} size={16} filled={isActive && hasRun} className="text-[var(--brand-500)]" />
                  {sc.label}
                </span>
                <span className="text-xs text-[var(--text-tertiary)]">{sc.desc}</span>
                <span className="text-[11px] text-[var(--text-tertiary)] numeric">
                  進 D{sc.entry > 0 ? "+" : ""}{sc.entry}・出 D{sc.exit > 0 ? "+" : ""}{sc.exit}
                </span>
              </Link>
            );
          })}
        </div>
      </section>

      <form method="GET" action="/event-backtest" className="rounded-xl border border-[var(--border-default)] bg-surface p-5 flex flex-col gap-4">
        <input type="hidden" name="run" value="1" />

        <Field label={`股票來源（自選 ${wl.length} / 自訂）`}>
          <div className="flex gap-4 text-sm">
            <label className="inline-flex items-center gap-1.5">
              <input type="radio" name="source" value="watchlist" defaultChecked={source !== "custom"} />自選股
            </label>
            <label className="inline-flex items-center gap-1.5">
              <input type="radio" name="source" value="custom" defaultChecked={source === "custom"} />自訂
            </label>
          </div>
        </Field>
        {source === "custom" && (
          <Field label="自訂代號（空白或逗號分隔，上限 100）">
            <textarea name="tickers" rows={2} defaultValue={customIds} placeholder="2330 2317 0050"
              className={cn(inputCls, "font-mono resize-y")} />
          </Field>
        )}

        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <Field label={`進場時點：D${entry > 0 ? "+" : ""}${entry}`} hint="負=事件前，正=事件後">
            <input name="entry" type="range" min={-20} max={5} step={1} defaultValue={entry} className={rangeCls} />
          </Field>
          <Field label={`出場時點：D${exit > 0 ? "+" : ""}${exit}`} hint="正=事件後">
            <input name="exit" type="range" min={1} max={60} step={1} defaultValue={exit} className={rangeCls} />
          </Field>
          <Field label={`起始年份：${sinceYear}`}>
            <input name="year" type="range" min={2015} max={2024} step={1} defaultValue={sinceYear} className={rangeCls} />
          </Field>
          <Field label={`最低現金股利：${minDiv}`} hint="過濾雜訊事件">
            <input name="minDiv" type="range" min={0} max={5} step={0.5} defaultValue={minDiv} className={rangeCls} />
          </Field>
        </div>

        <div className="flex items-center gap-3">
          <button type="submit" className={btnPrimary}><Icon name="play_arrow" size={18} filled />執行回測</button>
          <Link href="/event-backtest" className={btnSecondary}><Icon name="refresh" size={16} />重設</Link>
        </div>
      </form>

      {!hasRun ? (
        <EmptyState>設定參數後按「執行回測」</EmptyState>
      ) : errorMsg ? (
        <div className="rounded-xl border border-[var(--error-border)] bg-[var(--error-bg)] p-5 text-[var(--error-fg)] flex gap-3 items-start">
          <Icon name="error" size={20} filled />
          <div>
            <div className="font-semibold text-sm">執行失敗</div>
            <div className="text-xs mt-1 break-words leading-relaxed">{humanizeApiError(errorMsg)}</div>
          </div>
        </div>
      ) : result ? (
        <>
          <Conclusion result={result} />

          <section className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
            <KPIStat label="事件數" value={String(result.summary.nEvents)} footnote={`有資料 ${result.summary.nWithData}`} tone="neutral" />
            <KPIStat label="勝率" value={result.summary.winRate != null ? `${(result.summary.winRate * 100).toFixed(0)}%` : "—"} tone={(result.summary.winRate ?? 0) >= 0.5 ? "up" : "down"} />
            <KPIStat label="平均含息報酬" value={fmtPct(result.summary.avgTotalReturn, 2)} tone={tone(result.summary.avgTotalReturn)} />
            <KPIStat label="純價差" value={fmtPct(result.summary.avgPriceReturn, 2)} tone={tone(result.summary.avgPriceReturn)} footnote="不含息" />
            <KPIStat label="平均殖利率" value={fmtPct(result.summary.avgDividendYield, 2)} tone="up" />
            <KPIStat label="最佳 / 最差" value={`${fmtPct(result.summary.bestReturn, 1)} / ${fmtPct(result.summary.worstReturn, 1)}`} tone="neutral" />
          </section>

          {result.byStock.length > 0 && (
            <section className="flex flex-col gap-3">
              <h2 className="text-base font-semibold inline-flex items-center gap-2">
                <Icon name="leaderboard" size={20} className="text-[var(--brand-500)]" />
                每檔表現（依平均含息報酬降序）
                <span className="numeric text-xs text-[var(--text-tertiary)] font-normal ml-2">{result.byStock.length} 檔</span>
              </h2>
              <TableContainer>
                <table className="w-full text-[15px] min-w-[800px]">
                  <thead className="bg-subtle">
                    <tr>
                      <Th>代號 / 名稱</Th>
                      <Th align="right">事件數</Th>
                      <Th align="right">勝率</Th>
                      <Th align="right">平均含息報酬</Th>
                      <Th align="right">平均殖利率</Th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.byStock.map((s) => (
                      <tr key={s.stockId} className="border-t border-[var(--border-default)] hover:bg-subtle">
                        <Td>
                          <StockIdCell stockId={s.stockId} stockName={s.stockName} />
                        </Td>
                        <Td align="right" numeric>{s.nEvents}</Td>
                        <Td align="right" numeric>{s.winRate != null ? `${(s.winRate * 100).toFixed(0)}%` : "—"}</Td>
                        <Td align="right" numeric>
                          <span className={cn("font-semibold", tc(s.avgTotalReturn))}>{fmtPct(s.avgTotalReturn, 2)}</span>
                        </Td>
                        <Td align="right" numeric className="text-[var(--color-up)]">{fmtPct(s.avgDividendYield, 2)}</Td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableContainer>
            </section>
          )}

          {result.trades.length > 0 && (
            <section className="flex flex-col gap-3">
              <h2 className="text-base font-semibold inline-flex items-center gap-2">
                <Icon name="receipt_long" size={20} className="text-[var(--brand-500)]" />
                逐筆事件明細
                <span className="numeric text-xs text-[var(--text-tertiary)] font-normal ml-2">{result.trades.length} 筆</span>
              </h2>
              <TradesTable trades={result.trades} />
            </section>
          )}
        </>
      ) : (
        <EmptyState>所選股票在此期間找不到除權息事件。試著放寬「最低現金股利」或拉早「起始年份」。</EmptyState>
      )}
    </div>
  );
}

function Conclusion({ result }: { result: EventBacktestResponse }) {
  const { summary } = result;
  if (!summary.nWithData) {
    return (
      <div className="rounded-xl border border-[var(--warning-border)] bg-[var(--warning-bg)] p-4 text-sm text-[var(--warning-fg)] flex gap-3 items-start">
        <Icon name="info" size={18} filled />
        <div>
          <div className="font-semibold">沒有可用事件樣本</div>
          <div className="text-xs mt-1">所選股票在此區間沒有除權息紀錄，或 adj_event 還沒抓回。可跑 <code className="font-mono">python -m scripts.update_adj</code></div>
        </div>
      </div>
    );
  }
  const winsBoth = (summary.avgTotalReturn ?? 0) > 0 && (summary.winRate ?? 0) >= 0.5;
  const headline = winsBoth
    ? `這套「除權息套利」歷史可行 — 勝率 ${(summary.winRate! * 100).toFixed(0)}%、平均 +${(summary.avgTotalReturn! * 100).toFixed(2)}%`
    : (summary.avgTotalReturn ?? 0) < 0
    ? `這套「除權息套利」歷史不賺 — 平均 ${(summary.avgTotalReturn! * 100).toFixed(2)}%`
    : `結果模糊 — 勝率 ${(summary.winRate! * 100).toFixed(0)}%、平均 ${(summary.avgTotalReturn! * 100).toFixed(2)}%`;
  const palette = winsBoth
    ? "border-[var(--up-300)] bg-[var(--color-up-bg)] text-[var(--color-up)]"
    : (summary.avgTotalReturn ?? 0) < 0
    ? "border-[var(--error-border)] bg-[var(--error-bg)] text-[var(--error-fg)]"
    : "border-[var(--warning-border)] bg-[var(--warning-bg)] text-[var(--warning-fg)]";
  return (
    <div className={cn("rounded-xl border p-4 text-sm flex gap-3 items-start", palette)}>
      <Icon name={winsBoth ? "verified" : "warning"} size={20} filled />
      <div>
        <div className="font-semibold text-base">{headline}</div>
        <div className="text-xs mt-1 leading-relaxed text-[var(--text-secondary)]">
          {summary.nWithData}/{summary.nEvents} 筆有完整資料、平均殖利率 {(summary.avgDividendYield! * 100).toFixed(2)}%、
          最佳 +{(summary.bestReturn! * 100).toFixed(1)}%、最差 {(summary.worstReturn! * 100).toFixed(1)}%
          {summary.medianTotalReturn != null && `、中位數 ${(summary.medianTotalReturn * 100).toFixed(2)}%`}
        </div>
      </div>
    </div>
  );
}

function TradesTable({ trades }: { trades: EventTradeRow[] }) {
  return (
    <TableContainer>
      <table className="w-full text-[15px] min-w-[900px]">
        <thead className="bg-subtle">
          <tr>
            <Th>除息日</Th>
            <Th>代號 / 名稱</Th>
            <Th align="right">進場日</Th>
            <Th align="right">進場價</Th>
            <Th align="right">出場日</Th>
            <Th align="right">出場價</Th>
            <Th align="right">現金股利</Th>
            <Th align="right">純價差</Th>
            <Th align="right">含息報酬</Th>
          </tr>
        </thead>
        <tbody>
          {trades.map((t, i) => (
            <tr key={i} className="border-t border-[var(--border-default)] hover:bg-subtle">
              <Td numeric className="text-xs">{t.exDate}</Td>
              <Td>
                <StockIdCell stockId={t.stockId} stockName={t.stockName} />
              </Td>
              <Td align="right" numeric className="text-xs">{t.entryDate ?? "—"}</Td>
              <Td align="right" numeric>{t.entryPrice != null ? fmtPrice(t.entryPrice) : "—"}</Td>
              <Td align="right" numeric className="text-xs">{t.exitDate ?? "—"}</Td>
              <Td align="right" numeric>{t.exitPrice != null ? fmtPrice(t.exitPrice) : "—"}</Td>
              <Td align="right" numeric className="text-[var(--color-up)]">{t.cashDividend.toFixed(2)}</Td>
              <Td align="right" numeric><span className={tc(t.priceReturn)}>{fmtPct(t.priceReturn, 2)}</span></Td>
              <Td align="right" numeric><span className={cn("font-semibold", tc(t.totalReturn))}>{fmtPct(t.totalReturn, 2)}</span></Td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableContainer>
  );
}

