import Link from "next/link";
import { apiGet, apiPost, humanizeApiError, type BacktestResponse } from "@/lib/api";
import { Icon } from "@/components/primitives/Icon";
import { PageHeader } from "@/components/primitives/PageHeader";
import { SurvivorshipWarning } from "@/components/primitives/SurvivorshipWarning";
import { EmptyState } from "@/components/primitives/EmptyState";
import { KPIStat } from "@/components/primitives/KPIStat";
import { NextStepCards } from "@/components/primitives/NextStepCard";
import { BackendDownError } from "@/components/primitives/BackendDownError";
import { Field } from "@/components/primitives/Field";
import { Th, TdCompact as Td } from "@/components/primitives/Table";
import { TableContainer } from "@/components/primitives/TableContainer";
import { BacktestEquityChart } from "@/components/charts/BacktestEquityChartLazy";
import { fmtPct, fmtPrice, tone, toneClass } from "@/lib/format";
import { btnPrimary, btnSecondary, inputCls, rangeCls } from "@/lib/formClasses";
import { BACKTEST_SCENARIOS as SCENARIOS } from "@/lib/scenarios";
import { cn } from "@/lib/utils";

export const revalidate = 0;

const DEFAULTS = {
  entryThreshold: 65,
  exitThreshold: 40,
  stopLossPct: 0.08,
  takeProfitPct: 0.20,
  maxHoldDays: 60,
  slippageBps: 5,
  lookbackDays: 500,
};

// 情景預設：對應不同投資風格，避免使用者面對 7 個滑桿不知從哪試起
type WatchlistEntry = { stockId: string; stockName: string };

function num(v: string | undefined, fallback: number): number {
  if (v == null) return fallback;
  const n = Number(v);
  return Number.isFinite(n) ? n : fallback;
}

export default async function BacktestPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const sp = await searchParams;
  const stockId = (sp.stockId ?? "").trim();

  let entries: WatchlistEntry[];
  try {
    entries = await apiGet<WatchlistEntry[]>("/api/watchlist", { noCache: true });
  } catch (e) {
    return <BackendDownError error={e} pageTitle="策略回測" />;
  }

  const cfg = {
    entryThreshold: num(sp.entry, DEFAULTS.entryThreshold),
    exitThreshold: num(sp.exit, DEFAULTS.exitThreshold),
    stopLossPct: num(sp.sl, DEFAULTS.stopLossPct),
    takeProfitPct: num(sp.tp, DEFAULTS.takeProfitPct),
    maxHoldDays: num(sp.maxHold, DEFAULTS.maxHoldDays),
    slippageBps: num(sp.slippage, DEFAULTS.slippageBps),
    lookbackDays: num(sp.lookback, DEFAULTS.lookbackDays),
  };

  let result: BacktestResponse | null = null;
  let errorMsg: string | null = null;
  if (stockId) {
    try {
      result = await apiPost<BacktestResponse>("/api/backtest/stock", {
        stock_id: stockId,
        config: {
          entry_threshold: cfg.entryThreshold,
          exit_threshold: cfg.exitThreshold,
          stop_loss_pct: cfg.stopLossPct,
          take_profit_pct: cfg.takeProfitPct,
          max_hold_days: cfg.maxHoldDays,
          slippage_bps: cfg.slippageBps,
          lookback_days: cfg.lookbackDays,
          use_adj: true,
        },
      });
    } catch (e) {
      errorMsg = (e as Error).message;
    }
  }

  return (
    <div className="p-4 lg:p-8 flex flex-col gap-8 max-w-[1600px] mx-auto">
      <PageHeader
        title="策略回測"
        icon="replay"
        description="用過去資料模擬「短期分數高就買、低就賣」這套規則的歷史成績。回答「這策略在這檔股票過去兩年能賺多少？最大虧多少？」"
        extra="不熟參數的話：先選下方一個「情景預設」、輸入代號就能跑。"
      />
      <SurvivorshipWarning />

      {/* 情景預設 — 一鍵套用適合不同風格的參數組 */}
      <section className="flex flex-col gap-2">
        <h2 className="text-sm font-semibold text-[var(--text-secondary)] inline-flex items-center gap-1.5">
          <Icon name="palette" size={16} className="text-[var(--brand-500)]" />
          情景預設
          <span className="text-[11px] text-[var(--text-tertiary)] font-normal ml-1">（一鍵套用）</span>
        </h2>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          {(Object.entries(SCENARIOS) as [keyof typeof SCENARIOS, typeof SCENARIOS[keyof typeof SCENARIOS]][]).map(
            ([key, sc]) => {
              const isActive =
                cfg.entryThreshold === sc.cfg.entry &&
                cfg.exitThreshold === sc.cfg.exit &&
                Math.abs(cfg.stopLossPct - sc.cfg.sl) < 1e-6 &&
                Math.abs(cfg.takeProfitPct - sc.cfg.tp) < 1e-6 &&
                cfg.maxHoldDays === sc.cfg.maxHold;
              const q = new URLSearchParams({
                ...(stockId ? { stockId } : {}),
                entry: String(sc.cfg.entry),
                exit: String(sc.cfg.exit),
                sl: String(sc.cfg.sl),
                tp: String(sc.cfg.tp),
                maxHold: String(sc.cfg.maxHold),
                slippage: String(sc.cfg.slippage),
                lookback: String(sc.cfg.lookback),
              }).toString();
              return (
                <Link
                  key={key}
                  href={`/backtest?${q}`}
                  className={cn(
                    "rounded-xl border p-4 flex flex-col gap-1.5 transition-colors",
                    isActive
                      ? "border-[var(--brand-500)] bg-[var(--brand-tint)]"
                      : "border-[var(--border-default)] bg-surface hover:border-[var(--brand-300)]",
                  )}
                >
                  <span className="inline-flex items-center gap-1.5 text-sm font-semibold">
                    <Icon name={sc.icon} size={16} filled={isActive} className="text-[var(--brand-500)]" />
                    {sc.label}
                    {isActive && <span className="text-[11px] text-[var(--brand-500)] ml-auto">已套用</span>}
                  </span>
                  <span className="text-xs text-[var(--text-tertiary)]">{sc.desc}</span>
                  <span className="text-[11px] text-[var(--text-tertiary)] numeric">
                    進 {sc.cfg.entry}・出 {sc.cfg.exit}・停損 {(sc.cfg.sl * 100).toFixed(0)}%・停利 {(sc.cfg.tp * 100).toFixed(0)}%
                  </span>
                </Link>
              );
            },
          )}
        </div>
      </section>

      {/* 參數表單 */}
      <form method="GET" action="/backtest" className="rounded-xl border border-[var(--border-default)] bg-surface p-5 flex flex-col gap-4">
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
          <Field label="股票代號" hint="自選清單或任意代號">
            <div className="flex gap-2">
              <input
                name="stockId"
                type="text"
                defaultValue={stockId}
                placeholder="2330"
                list="wl-list"
                className={cn(inputCls, "flex-1")}
              />
              <datalist id="wl-list">
                {entries.map((e) => (
                  <option key={e.stockId} value={e.stockId}>{e.stockName}</option>
                ))}
              </datalist>
            </div>
          </Field>
          <Field label={`進場門檻：${cfg.entryThreshold}`} term="entry_threshold">
            <input name="entry" type="range" min={50} max={90} step={1} defaultValue={cfg.entryThreshold} className={rangeCls} />
          </Field>
          <Field label={`出場門檻：${cfg.exitThreshold}`} term="exit_threshold">
            <input name="exit" type="range" min={10} max={50} step={1} defaultValue={cfg.exitThreshold} className={rangeCls} />
          </Field>
          <Field label={`回測天數：${cfg.lookbackDays}`} term="lookback_days">
            <input name="lookback" type="range" min={100} max={900} step={50} defaultValue={cfg.lookbackDays} className={rangeCls} />
          </Field>
        </div>
        <details className="text-sm">
          <summary className="cursor-pointer text-[var(--text-secondary)] hover:text-[var(--text-primary)] inline-flex items-center gap-1.5 select-none">
            <Icon name="tune" size={14} />
            進階參數（停損 / 停利 / 持有天數 / 滑價）
          </summary>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 mt-3">
            <Field label={`停損：${(cfg.stopLossPct * 100).toFixed(1)}%`} term="stop_loss">
              <input name="sl" type="range" min={0.03} max={0.2} step={0.005} defaultValue={cfg.stopLossPct} className={rangeCls} />
            </Field>
            <Field label={`停利：${(cfg.takeProfitPct * 100).toFixed(1)}%`} term="take_profit">
              <input name="tp" type="range" min={0.05} max={0.5} step={0.01} defaultValue={cfg.takeProfitPct} className={rangeCls} />
            </Field>
            <Field label={`最長持有：${cfg.maxHoldDays} 天`} term="max_hold">
              <input name="maxHold" type="range" min={10} max={120} step={5} defaultValue={cfg.maxHoldDays} className={rangeCls} />
            </Field>
            <Field label={`滑價：${cfg.slippageBps} bps`} term="slippage">
              <input name="slippage" type="range" min={0} max={30} step={1} defaultValue={cfg.slippageBps} className={rangeCls} />
            </Field>
          </div>
        </details>
        <div className="flex items-center gap-3 pt-1">
          <button type="submit" className={btnPrimary}>
            <Icon name="play_arrow" size={18} filled />
            執行回測
          </button>
          <Link href="/backtest" className={btnSecondary}>
            <Icon name="refresh" size={16} />
            重設參數
          </Link>
          <span className="text-xs text-[var(--text-tertiary)] ml-auto">還原價：自動啟用</span>
        </div>
      </form>

      {/* 結果區 */}
      {!stockId ? (
        <EmptyState>輸入股票代號後按「執行回測」</EmptyState>
      ) : errorMsg ? (
        <div className="rounded-xl border border-[var(--error-border)] bg-[var(--error-bg)] p-5 text-[var(--error-fg)] flex gap-3 items-start">
          <Icon name="error" size={20} filled />
          <div>
            <div className="font-semibold text-sm">回測失敗</div>
            <div className="text-xs mt-1 break-words leading-relaxed">{humanizeApiError(errorMsg)}</div>
          </div>
        </div>
      ) : result && (
        <>
          {/* Header 股票名 */}
          <section>
            <h2 className="text-lg font-semibold inline-flex items-center gap-2">
              <Icon name="monitoring" size={20} className="text-[var(--brand-500)]" />
              <span className="numeric">{result.summary.stockId}</span>
              <span className="text-[var(--text-secondary)]">{result.summary.stockName}</span>
            </h2>
          </section>

          {/* 一句話結論卡 — 把多項數字翻成白話 */}
          <ConclusionCard summary={result.summary} />

          {/* Summary KPIs */}
          <section className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
            <KPIStat
              label="交易次數"
              value={result.summary.nTrades.toString()}
              tone="neutral"
            />
            <KPIStat
              label="勝率"
              value={result.summary.nTrades === 0 ? "—" : `${(result.summary.winRate * 100).toFixed(1)}%`}
              tone={result.summary.winRate >= 0.5 ? "up" : result.summary.winRate > 0 ? "down" : "neutral"}
              term="win_rate"
            />
            <KPIStat
              label="平均每筆"
              value={result.summary.nTrades === 0 ? "—" : fmtPct(result.summary.avgReturn, 2)}
              tone={tone(result.summary.avgReturn)}
            />
            <KPIStat
              label="累積報酬"
              value={fmtPct(result.summary.totalReturn, 2)}
              tone={tone(result.summary.totalReturn)}
              term="total_return"
            />
            <KPIStat
              label="最大回撤"
              value={fmtPct(result.summary.maxDrawdown, 2)}
              tone={result.summary.maxDrawdown < 0 ? "down" : "neutral"}
              term="max_drawdown"
            />
            <KPIStat
              label="Alpha"
              value={fmtPct(result.summary.alpha, 2)}
              tone={tone(result.summary.alpha)}
              footnote={`vs 買進持有 ${fmtPct(result.summary.buyAndHold, 2)}（已扣同等費用）`}
              term="alpha"
            />
          </section>

          {/* 風險調整指標 — Sharpe / Sortino / Calmar */}
          <section className="grid grid-cols-3 gap-3">
            <KPIStat
              label="Sharpe"
              value={result.summary.sharpe == null ? "—" : result.summary.sharpe.toFixed(2)}
              tone={
                result.summary.sharpe == null
                  ? "neutral"
                  : result.summary.sharpe > 0.5
                  ? "up"
                  : result.summary.sharpe < 0
                  ? "down"
                  : "neutral"
              }
              footnote="每筆 mean / std；> 0.5 算不錯"
            />
            <KPIStat
              label="Sortino"
              value={result.summary.sortino == null ? "—" : result.summary.sortino.toFixed(2)}
              tone={
                result.summary.sortino == null
                  ? "neutral"
                  : result.summary.sortino > 0.5
                  ? "up"
                  : result.summary.sortino < 0
                  ? "down"
                  : "neutral"
              }
              footnote="只算下行波動；比 Sharpe 嚴格"
            />
            <KPIStat
              label="Calmar"
              value={result.summary.calmar == null ? "—" : result.summary.calmar.toFixed(2)}
              tone={
                result.summary.calmar == null
                  ? "neutral"
                  : result.summary.calmar > 1
                  ? "up"
                  : result.summary.calmar < 0
                  ? "down"
                  : "neutral"
              }
              footnote="累積報酬 / |最大回撤|；> 1 即賺得回所付的最壞虧損"
            />
          </section>

          {/* Equity chart */}
          <section className="flex flex-col gap-3">
            <h2 className="text-base font-semibold inline-flex items-center gap-2">
              <Icon name="show_chart" size={20} className="text-[var(--brand-500)]" />
              價格與進出場點
            </h2>
            <div className="rounded-xl border border-[var(--border-default)] bg-surface p-5">
              <BacktestEquityChart daily={result.dailySeries} trades={result.trades} height={360} />
              <div className="flex gap-4 mt-3 px-2 text-xs text-[var(--text-tertiary)]">
                <LegendDot color="var(--brand-500)" label="收盤價" />
                <LegendDot color="var(--up-500)" label="進場" />
                <LegendDot color="var(--up-600)" label="獲利出場" />
                <LegendDot color="var(--down-600)" label="虧損出場" />
              </div>
            </div>
          </section>

          {/* Trades */}
          <section className="flex flex-col gap-3">
            <h2 className="text-base font-semibold inline-flex items-center gap-2">
              <Icon name="swap_horiz" size={20} className="text-[var(--brand-500)]" />
              交易明細
              <span className="numeric text-xs text-[var(--text-tertiary)] font-normal ml-2">共 {result.trades.length} 筆</span>
            </h2>
            {result.trades.length === 0 ? (
              <EmptyState size="sm" tone="secondary" className="leading-relaxed">
                回測期間從未觸發進場條件。
                <div className="text-xs text-[var(--text-tertiary)] mt-2">
                  通常代表 ① 進場門檻設太嚴（試試從 65 → 55）<br />
                  ② 回測期間這檔股票分數一直偏低（看「個股詳情」確認）<br />
                  ③ 回測天數太短沒涵蓋到強勢時期（拉長到 700 天）
                </div>
              </EmptyState>
            ) : (
              <TableContainer>
                <table className="w-full text-[15px] min-w-[700px]">
                  <thead className="bg-subtle">
                    <tr>
                      <Th>進場日</Th>
                      <Th>出場日</Th>
                      <Th align="right">持有天數</Th>
                      <Th align="right">進場價</Th>
                      <Th align="right">出場價</Th>
                      <Th align="right">毛報酬</Th>
                      <Th align="right">淨報酬</Th>
                      <Th>出場原因</Th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.trades.map((t) => (
                      <tr key={`${t.entryDate}-${t.exitDate}`} className="border-t border-[var(--border-default)] hover:bg-subtle">
                        <Td numeric>{t.entryDate}</Td>
                        <Td numeric>{t.exitDate}</Td>
                        <Td align="right" numeric>{t.holdDays}</Td>
                        <Td align="right" numeric>{fmtPrice(t.entryPrice)}</Td>
                        <Td align="right" numeric>{fmtPrice(t.exitPrice)}</Td>
                        <Td align="right" numeric>
                          <span className={toneClass(t.grossReturn)}>{fmtPct(t.grossReturn, 2)}</span>
                        </Td>
                        <Td align="right" numeric>
                          <span className={cn("font-semibold", toneClass(t.netReturn))}>{fmtPct(t.netReturn, 2)}</span>
                        </Td>
                        <Td>
                          <ExitReason reason={t.exitReason} />
                        </Td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableContainer>
            )}
          </section>

          {/* 套用的 config */}
          <section className="text-xs text-[var(--text-tertiary)]">
            <details>
              <summary className="cursor-pointer hover:text-[var(--text-secondary)]">套用的完整設定</summary>
              <pre className="mt-2 p-3 rounded bg-subtle overflow-x-auto font-mono text-[11px]">
                {JSON.stringify(result.config, null, 2)}
              </pre>
            </details>
          </section>

          {/* 下一步建議 */}
          <NextStepCards items={[
            {
              href: `/portfolio-backtest?entry=${cfg.entryThreshold}&exit=${cfg.exitThreshold}`,
              icon: "groups",
              title: "套到一籃子股票",
              description: "同樣參數對自選股或自訂清單跑投組回測，看整體績效與大盤對照。",
            },
            {
              href: `/grid-search?stockId=${stockId}`,
              icon: "tune",
              title: "找最佳參數組合",
              description: "進場/出場/停損/停利掃描，找出歷史上最賺的一組（含過擬合警示）。",
            },
            {
              href: `/stocks/${stockId}`,
              icon: "monitoring",
              title: "回個股詳情",
              description: "看這檔股票最新分數、評分拆解、進出場建議。",
            },
          ]} />
        </>
      )}
    </div>
  );
}

function ConclusionCard({ summary }: { summary: BacktestResponse["summary"] }) {
  const { alpha, totalReturn, winRate, maxDrawdown, nTrades, buyAndHold } = summary;

  if (nTrades === 0) {
    return (
      <div className="rounded-xl border border-[var(--warning-border)] bg-[var(--warning-bg)] p-4 text-sm text-[var(--warning-fg)] flex gap-3 items-start">
        <Icon name="info" size={18} filled />
        <div>
          <div className="font-semibold">回測期間沒有觸發任何交易</div>
          <div className="text-xs mt-1 leading-relaxed">這檔股票分數可能一直偏低、或進場門檻設得太嚴。試試「長期持有」情景或把進場門檻調到 55。</div>
        </div>
      </div>
    );
  }

  const winsBH = alpha != null && alpha > 0;
  const verdict = winsBH
    ? { tone: "ok", icon: "verified", title: `贏過買進持有 +${(alpha * 100).toFixed(1)}%` }
    : { tone: "warn", icon: "do_not_disturb_on", title: `輸給買進持有 ${(alpha! * 100).toFixed(1)}%` };
  const profitable = totalReturn != null && totalReturn > 0;
  const ddSevere = maxDrawdown != null && maxDrawdown < -0.20;

  const palette = verdict.tone === "ok"
    ? "border-[var(--up-300)] bg-[var(--color-up-bg)] text-[var(--color-up)]"
    : "border-[var(--warning-border)] bg-[var(--warning-bg)] text-[var(--warning-fg)]";

  return (
    <div className={cn("rounded-xl border p-4 text-sm flex gap-3 items-start", palette)}>
      <Icon name={verdict.icon} size={20} filled />
      <div className="flex-1">
        <div className="font-semibold text-base">{verdict.title}</div>
        <div className="text-xs mt-1 leading-relaxed text-[var(--text-secondary)]">
          回測 <span className="numeric font-semibold">{nTrades}</span> 筆交易，勝率 {(winRate * 100).toFixed(0)}%、
          策略累積 {profitable ? "+" : ""}{(totalReturn * 100).toFixed(1)}%、
          買進持有 {(buyAndHold * 100).toFixed(1)}%、
          最大回撤 {(maxDrawdown * 100).toFixed(1)}%
          {ddSevere && <span className="text-[var(--color-down)] font-medium">（回撤偏深，部位要控制）</span>}
        </div>
      </div>
    </div>
  );
}

const REASON_LABEL: Record<string, { label: string; cls: string }> = {
  stop_loss: { label: "停損", cls: "bg-[var(--color-down-bg)] text-[var(--color-down)]" },
  take_profit: { label: "停利", cls: "bg-[var(--color-up-bg)] text-[var(--color-up)]" },
  score_exit: { label: "分數出場", cls: "bg-subtle text-[var(--text-secondary)]" },
  max_hold: { label: "到期", cls: "bg-[var(--warning-bg)] text-[var(--warning-fg)]" },
};

function ExitReason({ reason }: { reason: string }) {
  const r = REASON_LABEL[reason] ?? { label: reason, cls: "bg-subtle text-[var(--text-secondary)]" };
  return (
    <span className={cn("inline-flex items-center px-2 py-0.5 rounded text-[11px] font-medium", r.cls)}>
      {r.label}
    </span>
  );
}

function LegendDot({ color, label }: { color: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="inline-block w-3 h-3 rounded-full" style={{ backgroundColor: color }} />
      {label}
    </span>
  );
}

