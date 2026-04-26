import Link from "next/link";
import { apiGet, apiPost, humanizeApiError, type PortfolioBacktestResponse } from "@/lib/api";
import { Icon } from "@/components/primitives/Icon";
import { PageHeader } from "@/components/primitives/PageHeader";
import { SurvivorshipWarning } from "@/components/primitives/SurvivorshipWarning";
import { EmptyState } from "@/components/primitives/EmptyState";
import { KPIStat } from "@/components/primitives/KPIStat";
import { InfoTip } from "@/components/primitives/InfoTip";
import { NextStepCards } from "@/components/primitives/NextStepCard";
import { BackendDownError } from "@/components/primitives/BackendDownError";
import { Field } from "@/components/primitives/Field";
import { Th, TdCompact as Td } from "@/components/primitives/Table";
import { TableContainer } from "@/components/primitives/TableContainer";
import { StockIdCell } from "@/components/primitives/StockIdCell";
import { DownloadCsvButton } from "@/components/primitives/DownloadCsvButton";
import { fmtPct, tone, toneClass as tc } from "@/lib/format";
import { btnPrimary, btnSecondary, inputCls, rangeCls } from "@/lib/formClasses";
import { BACKTEST_SCENARIOS as SCENARIOS } from "@/lib/scenarios";
import { cn } from "@/lib/utils";

export const revalidate = 0;

type WatchlistEntry = { stockId: string; stockName: string };

function num(v: string | undefined, fb: number): number {
  if (v == null) return fb;
  const n = Number(v);
  return Number.isFinite(n) ? n : fb;
}

const DEFAULTS = {
  entry: 65, exit: 40, sl: 0.08, tp: 0.20,
  maxHold: 60, slippage: 5, lookback: 500,
};

export default async function PortfolioBacktestPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const sp = await searchParams;
  const source = (sp.source ?? "watchlist") as "watchlist" | "custom";
  const customIds = (sp.tickers ?? "").trim();
  const cfg = {
    entry: num(sp.entry, DEFAULTS.entry),
    exit: num(sp.exit, DEFAULTS.exit),
    sl: num(sp.sl, DEFAULTS.sl),
    tp: num(sp.tp, DEFAULTS.tp),
    maxHold: num(sp.maxHold, DEFAULTS.maxHold),
    slippage: num(sp.slippage, DEFAULTS.slippage),
    lookback: num(sp.lookback, DEFAULTS.lookback),
  };

  let wl: WatchlistEntry[];
  try {
    wl = await apiGet<WatchlistEntry[]>("/api/watchlist", { noCache: true });
  } catch (e) {
    return <BackendDownError error={e} pageTitle="投組回測" />;
  }
  const hasRun = !!sp.run;

  let stockIds: string[] = [];
  if (hasRun) {
    stockIds = source === "custom"
      ? customIds.split(/[\s,，]+/).map((s) => s.trim()).filter(Boolean)
      : wl.map((e) => e.stockId);
  }

  let result: PortfolioBacktestResponse | null = null;
  let errorMsg: string | null = null;
  if (hasRun && stockIds.length > 0) {
    try {
      result = await apiPost<PortfolioBacktestResponse>("/api/backtest/portfolio", {
        stock_ids: stockIds,
        config: {
          entry_threshold: cfg.entry,
          exit_threshold: cfg.exit,
          stop_loss_pct: cfg.sl,
          take_profit_pct: cfg.tp,
          max_hold_days: cfg.maxHold,
          slippage_bps: cfg.slippage,
          lookback_days: cfg.lookback,
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
        title="多檔等權平均回測"
        icon="bar_chart"
        description="同一套策略對一籃子股票各別獨立回測，再取等權平均。回答「這策略在自選股上整體表現如何」。"
        extra="不熟參數的話：先選一個「情景預設」、按「執行回測」就好。預設用自選股一籃子。上限 50 檔，耗時約 0.5-3 秒/檔。"
      />
      <div
        className="rounded-lg border border-[var(--warning-border)] bg-[var(--warning-bg)] text-[var(--warning-fg)] px-4 py-3 text-xs leading-relaxed"
        role="note"
      >
        <strong>⚠ 這不是真實 portfolio backtest：</strong>
        各檔獨立計算後等權平均，沒有共用資金帳戶、沒有持倉上限、沒有再平衡。
        若兩檔同時觸發進場訊號，這裡視為兩檔都「滿倉買入」，現金約束被忽略。
        想評估的是「策略本身的平均勝率與報酬」，不能直接視為「拿一筆錢分散到 N 檔」的實際組合績效。
      </div>
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
            const isActive =
              cfg.entry === sc.cfg.entry &&
              cfg.exit === sc.cfg.exit &&
              Math.abs(cfg.sl - sc.cfg.sl) < 1e-6 &&
              Math.abs(cfg.tp - sc.cfg.tp) < 1e-6 &&
              cfg.maxHold === sc.cfg.maxHold;
            const q = new URLSearchParams({
              run: "1",
              source,
              ...(source === "custom" && customIds ? { tickers: customIds } : {}),
              entry: String(sc.cfg.entry), exit: String(sc.cfg.exit),
              sl: String(sc.cfg.sl), tp: String(sc.cfg.tp),
              maxHold: String(sc.cfg.maxHold), slippage: String(sc.cfg.slippage),
              lookback: String(sc.cfg.lookback),
            }).toString();
            return (
              <Link
                key={key}
                href={`/portfolio-backtest?${q}`}
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
                  {isActive && hasRun && <span className="text-[11px] text-[var(--brand-500)] ml-auto">已套用</span>}
                </span>
                <span className="text-xs text-[var(--text-tertiary)]">{sc.desc}</span>
                <span className="text-[11px] text-[var(--text-tertiary)] numeric">
                  進 {sc.cfg.entry}・出 {sc.cfg.exit}・停損 {(sc.cfg.sl*100).toFixed(0)}%・停利 {(sc.cfg.tp*100).toFixed(0)}%
                </span>
              </Link>
            );
          })}
        </div>
      </section>

      <form method="GET" action="/portfolio-backtest" className="rounded-xl border border-[var(--border-default)] bg-surface p-5 flex flex-col gap-4">
        <input type="hidden" name="run" value="1" />

        <Field label={`股票來源（自選清單 ${wl.length} 檔 / 自訂）`}>
          <div className="flex gap-4 text-sm">
            <label className="inline-flex items-center gap-1.5">
              <input type="radio" name="source" value="watchlist" defaultChecked={source !== "custom"} />
              自選股
            </label>
            <label className="inline-flex items-center gap-1.5">
              <input type="radio" name="source" value="custom" defaultChecked={source === "custom"} />
              自訂
            </label>
          </div>
        </Field>

        {source === "custom" && (
          <Field label="自訂代號（一行一個，或用空白/逗號分隔，上限 50）">
            <textarea
              name="tickers"
              rows={3}
              defaultValue={customIds}
              placeholder="2330 2317 2454"
              className={cn(inputCls, "font-mono resize-y")}
            />
          </Field>
        )}

        <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
          <Field label={`進場：${cfg.entry}`} term="entry_threshold"><input name="entry" type="range" min={50} max={90} step={1} defaultValue={cfg.entry} className={rangeCls} /></Field>
          <Field label={`出場：${cfg.exit}`} term="exit_threshold"><input name="exit" type="range" min={10} max={50} step={1} defaultValue={cfg.exit} className={rangeCls} /></Field>
          <Field label={`回測天數：${cfg.lookback}`} term="lookback_days"><input name="lookback" type="range" min={100} max={900} step={50} defaultValue={cfg.lookback} className={rangeCls} /></Field>
        </div>
        <details className="text-sm">
          <summary className="cursor-pointer text-[var(--text-secondary)] hover:text-[var(--text-primary)] inline-flex items-center gap-1.5 select-none">
            <Icon name="tune" size={14} />
            進階參數（停損 / 停利 / 持有天數 / 滑價）
          </summary>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mt-3">
            <Field label={`停損：${(cfg.sl*100).toFixed(1)}%`} term="stop_loss"><input name="sl" type="range" min={0.03} max={0.2} step={0.005} defaultValue={cfg.sl} className={rangeCls} /></Field>
            <Field label={`停利：${(cfg.tp*100).toFixed(1)}%`} term="take_profit"><input name="tp" type="range" min={0.05} max={0.5} step={0.01} defaultValue={cfg.tp} className={rangeCls} /></Field>
            <Field label={`最長持有：${cfg.maxHold} 天`} term="max_hold"><input name="maxHold" type="range" min={10} max={120} step={5} defaultValue={cfg.maxHold} className={rangeCls} /></Field>
            <Field label={`滑價：${cfg.slippage} bps`} term="slippage"><input name="slippage" type="range" min={0} max={30} step={1} defaultValue={cfg.slippage} className={rangeCls} /></Field>
          </div>
        </details>

        <div className="flex items-center gap-3">
          <button type="submit" className={btnPrimary}>
            <Icon name="play_arrow" size={18} filled />執行回測
          </button>
          <Link href="/portfolio-backtest" className={btnSecondary}>
            <Icon name="refresh" size={16} />重設
          </Link>
        </div>
      </form>

      {!hasRun ? (
        <EmptyState>設定參數後按「執行回測」</EmptyState>
      ) : errorMsg ? (
        <div className="rounded-xl border border-[var(--error-border)] bg-[var(--error-bg)] p-5 text-[var(--error-fg)] flex gap-3 items-start">
          <Icon name="error" size={20} filled />
          <div>
            <div className="font-semibold text-sm">回測失敗</div>
            <div className="text-xs mt-1 break-words leading-relaxed">{humanizeApiError(errorMsg)}</div>
          </div>
        </div>
      ) : result ? (
        <>
          {/* 一句話結論 */}
          <PortfolioConclusion summary={result.summary} />

          <section className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
            <KPIStat label="回測檔數" value={`${result.summary.nStocks}`} footnote={`有交易 ${result.summary.nWithTrades}`} tone="neutral" />
            <KPIStat label="加權勝率" value={fmtPct(result.summary.overallWinrate, 1)} tone={result.summary.overallWinrate>=0.5?"up":"down"} term="win_rate" />
            <KPIStat label="平均策略報酬" value={fmtPct(result.summary.avgStrategyReturn, 2)} tone={tone(result.summary.avgStrategyReturn)} />
            <KPIStat label="平均 B&H" value={fmtPct(result.summary.avgBuyAndHold, 2)} tone={tone(result.summary.avgBuyAndHold)} term="buy_and_hold" />
            <KPIStat label="平均 Alpha" value={fmtPct(result.summary.avgAlpha, 2)} tone={tone(result.summary.avgAlpha)} term="alpha" />
            <KPIStat label="0050 B&H" value={fmtPct(result.summary.bm0050, 2)} tone={tone(result.summary.bm0050)} footnote={`加權 ${fmtPct(result.summary.bmTaiex, 2)}`} />
          </section>

          <section className="flex flex-col gap-3">
            <div className="flex items-center justify-between gap-3 flex-wrap">
              <h2 className="text-base font-semibold inline-flex items-center gap-2">
                <Icon name="table_view" size={20} className="text-[var(--brand-500)]" />
                每檔明細（依 Alpha 降序）
                <span className="numeric text-xs text-[var(--text-tertiary)] font-normal ml-2">共 {result.rows.length} 檔</span>
              </h2>
              <DownloadCsvButton
                headers={PORTFOLIO_BT_CSV_HEADERS}
                rows={result.rows.map((r) => [
                  r.stockId, r.stockName ?? "", r.nTrades, r.winRate,
                  r.totalReturn, r.buyAndHold, r.alpha,
                  r.alphaVs0050 ?? "", r.alphaVsTaiex ?? "", r.maxDrawdown,
                ])}
                filename={`portfolio_backtest_${result.startDate ?? ""}_${result.endDate ?? ""}`}
                size="sm"
              />
            </div>
            <TableContainer>
              <table className="w-full text-[15px] min-w-[900px]">
                <thead className="bg-subtle">
                  <tr>
                    <Th>代號 / 名稱</Th>
                    <Th align="right">交易次數</Th>
                    <Th align="right"><span className="inline-flex items-center gap-1">勝率<InfoTip term="win_rate" /></span></Th>
                    <Th align="right"><span className="inline-flex items-center gap-1">累積報酬<InfoTip term="total_return" /></span></Th>
                    <Th align="right"><span className="inline-flex items-center gap-1">B&H<InfoTip term="buy_and_hold" /></span></Th>
                    <Th align="right"><span className="inline-flex items-center gap-1">Alpha<InfoTip term="alpha" /></span></Th>
                    <Th align="right"><span className="inline-flex items-center gap-1">vs 0050<InfoTip term="alpha_vs_0050" /></span></Th>
                    <Th align="right"><span className="inline-flex items-center gap-1">vs 加權<InfoTip term="alpha_vs_taiex" /></span></Th>
                    <Th align="right"><span className="inline-flex items-center gap-1">最大回撤<InfoTip term="max_drawdown" /></span></Th>
                  </tr>
                </thead>
                <tbody>
                  {result.rows.map((r) => (
                    <tr key={r.stockId} className="border-t border-[var(--border-default)] hover:bg-subtle">
                      <Td>
                        <StockIdCell stockId={r.stockId} stockName={r.stockName} />
                      </Td>
                      <Td align="right" numeric>{r.nTrades}</Td>
                      <Td align="right" numeric>{r.nTrades===0?"—":fmtPct(r.winRate, 1)}</Td>
                      <Td align="right" numeric><span className={tc(r.totalReturn)}>{fmtPct(r.totalReturn, 2)}</span></Td>
                      <Td align="right" numeric><span className={tc(r.buyAndHold)}>{fmtPct(r.buyAndHold, 2)}</span></Td>
                      <Td align="right" numeric><span className={cn("font-semibold", tc(r.alpha))}>{fmtPct(r.alpha, 2)}</span></Td>
                      <Td align="right" numeric><span className={tc(r.alphaVs0050)}>{fmtPct(r.alphaVs0050, 2)}</span></Td>
                      <Td align="right" numeric><span className={tc(r.alphaVsTaiex)}>{fmtPct(r.alphaVsTaiex, 2)}</span></Td>
                      <Td align="right" numeric><span className={tc(r.maxDrawdown)}>{fmtPct(r.maxDrawdown, 2)}</span></Td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableContainer>
            <p className="text-xs text-[var(--text-tertiary)]">
              回測區間 {result.startDate} → {result.endDate}
            </p>
          </section>

          <NextStepCards items={[
            {
              href: `/grid-search?entryList=${cfg.entry - 5},${cfg.entry},${cfg.entry + 5}&exitList=${cfg.exit - 5},${cfg.exit}`,
              icon: "tune",
              title: "找最佳參數",
              description: "對這組股票掃描進場/出場/停損/停利組合，找出歷史上最賺的一套參數。",
            },
            {
              href: "/weight-tuner",
              icon: "tune",
              title: "調整評分權重",
              description: "如果分數不符合直覺，調整短/中/長期 19 個子指標的權重，看評分怎麼變。",
            },
            {
              href: "/radar",
              icon: "radar",
              title: "看雷達掃描",
              description: "全市場 ~2300 檔依策略過濾的命中名單，找出更多候選股。",
            },
          ]} />
        </>
      ) : null}
    </div>
  );
}


function PortfolioConclusion({ summary }: { summary: PortfolioBacktestResponse["summary"] }) {
  const { avgAlpha, avgStrategyReturn, avgBuyAndHold, overallWinrate, bm0050, nWithTrades, nStocks } = summary;

  if (!nWithTrades) {
    return (
      <div className="rounded-xl border border-[var(--warning-border)] bg-[var(--warning-bg)] p-4 text-sm text-[var(--warning-fg)] flex gap-3 items-start">
        <Icon name="info" size={18} filled />
        <div>
          <div className="font-semibold">沒有任何股票觸發交易</div>
          <div className="text-xs mt-1 leading-relaxed">這組參數對這批股票太嚴。試試「長期持有」情景或把進場門檻調到 55。</div>
        </div>
      </div>
    );
  }

  const winsBM = avgAlpha != null && avgAlpha > 0;
  const winsBH = avgStrategyReturn != null && avgBuyAndHold != null && avgStrategyReturn > avgBuyAndHold;
  const tone = winsBM && winsBH ? "ok" : winsBH ? "warn" : "bad";
  const palette = tone === "ok"
    ? "border-[var(--up-300)] bg-[var(--color-up-bg)] text-[var(--color-up)]"
    : tone === "warn"
    ? "border-[var(--warning-border)] bg-[var(--warning-bg)] text-[var(--warning-fg)]"
    : "border-[var(--error-border)] bg-[var(--error-bg)] text-[var(--error-fg)]";
  const icon = tone === "ok" ? "verified" : tone === "warn" ? "warning" : "do_not_disturb_on";
  const bm0050Str = bm0050 != null ? `${(bm0050 * 100).toFixed(1)}%` : "—";
  const headline =
    tone === "ok" ? `策略整體贏，平均超額報酬 +${(avgAlpha * 100).toFixed(1)}%`
    : tone === "warn" ? `策略贏 B&H 但輸大盤，平均 Alpha ${(avgAlpha * 100).toFixed(1)}%`
    : `策略沒贏過大盤（0050 ${bm0050Str}）— 不如躺平`;

  return (
    <div className={cn("rounded-xl border p-4 text-sm flex gap-3 items-start", palette)}>
      <Icon name={icon} size={20} filled />
      <div className="flex-1">
        <div className="font-semibold text-base">{headline}</div>
        <div className="text-xs mt-1 leading-relaxed text-[var(--text-secondary)]">
          {nWithTrades}/{nStocks} 檔有交易、加權勝率 {(overallWinrate * 100).toFixed(0)}%，
          平均策略 {(avgStrategyReturn * 100).toFixed(1)}% vs 買進持有 {(avgBuyAndHold * 100).toFixed(1)}%、
          0050 同期 {bm0050Str}
        </div>
      </div>
    </div>
  );
}

const PORTFOLIO_BT_CSV_HEADERS = [
  "代號", "名稱", "交易次數", "勝率",
  "累積報酬", "B&H", "Alpha",
  "vs 0050", "vs 加權", "最大回撤",
];
