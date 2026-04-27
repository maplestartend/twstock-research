import Link from "next/link";
import { apiGet, apiPost, humanizeApiError, type GridSearchResponse, type WalkForwardResponse } from "@/lib/api";
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
import { fmtPct, tone, toneClass as tc } from "@/lib/format";
import { btnPrimary, btnSecondary, inputCls, rangeCls } from "@/lib/formClasses";
import { cn } from "@/lib/utils";

export const revalidate = 0;

type WatchlistEntry = { stockId: string; stockName: string };

function parseList(v: string | undefined, fallback: number[]): number[] {
  if (!v) return fallback;
  const arr = v.split(/[\s,，]+/).map((x) => Number(x.trim())).filter((n) => Number.isFinite(n));
  return arr.length ? arr : fallback;
}

function num(v: string | undefined, fb: number): number {
  if (v == null) return fb;
  const n = Number(v);
  return Number.isFinite(n) ? n : fb;
}

const DEFAULTS = {
  entries: [60, 65, 70],
  exits: [35, 40],
  sls: [0.08, 0.10],
  tps: [0.15, 0.20],
  lookback: 500,
  maxHold: 60,
};

// 掃描範圍預設：避免使用者手填逗號分隔的數字導致組合爆炸
const SCAN_PRESETS = {
  small: {
    label: "保守",
    desc: "範圍小、跑得快",
    icon: "speed",
    combos: 8,
    cfg: { entries: [60, 65], exits: [35, 40], sls: [0.08], tps: [0.20] },
  },
  balanced: {
    label: "平衡",
    desc: "預設值，覆蓋常用範圍",
    icon: "balance",
    combos: 24,
    cfg: { entries: [60, 65, 70], exits: [35, 40], sls: [0.08, 0.10], tps: [0.15, 0.20] },
  },
  wide: {
    label: "激進",
    desc: "範圍寬、找極值",
    icon: "explore",
    combos: 48,
    cfg: { entries: [55, 60, 65, 70], exits: [30, 35, 40], sls: [0.06, 0.10], tps: [0.15, 0.25] },
  },
} as const;

function arrEq(a: number[], b: readonly number[]): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    // 已用 length 比對保證 i 在範圍內，但 noUncheckedIndexedAccess 不知道
    const av = a[i] as number;
    const bv = b[i] as number;
    if (Math.abs(av - bv) > 1e-6) return false;
  }
  return true;
}

export default async function GridSearchPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const sp = await searchParams;
  const source = (sp.source ?? "watchlist") as "watchlist" | "custom";
  const customIds = (sp.tickers ?? "").trim();
  const entries = parseList(sp.entries, DEFAULTS.entries);
  const exits = parseList(sp.exits, DEFAULTS.exits);
  const sls = parseList(sp.sls, DEFAULTS.sls);
  const tps = parseList(sp.tps, DEFAULTS.tps);
  // 動態停利 K 候選（Chandelier ATR 倍數）。空 = 不開動態停利、走原本 4D 網格
  const tpKs = parseList(sp.tpKs, []);
  const lookback = num(sp.lookback, DEFAULTS.lookback);
  const maxHold = num(sp.maxHold, DEFAULTS.maxHold);
  const combos = entries.length * exits.length * sls.length * tps.length * (tpKs.length || 1);

  let wl: WatchlistEntry[];
  try {
    wl = await apiGet<WatchlistEntry[]>("/api/watchlist", { noCache: true });
  } catch (e) {
    return <BackendDownError error={e} pageTitle="參數掃描" />;
  }

  const hasRun = !!sp.run;
  const mode = (sp.mode ?? "grid") as "grid" | "wf";

  let stockIds: string[] = [];
  if (hasRun) {
    stockIds = source === "custom"
      ? customIds.split(/[\s,，]+/).map((s) => s.trim()).filter(Boolean)
      : wl.map((e) => e.stockId);
  }

  let gridResult: GridSearchResponse | null = null;
  let wfResult: WalkForwardResponse | null = null;
  let errorMsg: string | null = null;

  if (hasRun && stockIds.length > 0) {
    try {
      if (mode === "wf") {
        wfResult = await apiPost<WalkForwardResponse>("/api/backtest/walk-forward", {
          stock_ids: stockIds,
          entry_list: entries, exit_list: exits, sl_list: sls, tp_list: tps,
          max_hold_days: maxHold, n_splits: num(sp.splits, 3), train_ratio: num(sp.train, 0.7),
        });
      } else {
        gridResult = await apiPost<GridSearchResponse>("/api/backtest/grid-search", {
          stock_ids: stockIds,
          entry_list: entries, exit_list: exits, sl_list: sls, tp_list: tps,
          trailing_tp_k_list: tpKs,
          max_hold_days: maxHold, lookback_days: lookback,
        });
      }
    } catch (e) {
      errorMsg = (e as Error).message;
    }
  }

  return (
    <div className="p-4 lg:p-8 flex flex-col gap-8 max-w-[1600px] mx-auto">
      <PageHeader
        title="參數掃描"
        icon="science"
        description="幫你找出「進場/出場/停損/停利」最賺的數字組合。回答「我憑什麼相信進場 65 比 60 好？」"
        extra={<>兩種模式：<strong>網格掃描</strong>看歷史哪組最賺；<strong>Walk-Forward</strong>更嚴謹（會檢查找出的參數能不能在新資料延續，並用 Sharpe 取代 mean-return 選參數）。</>}
      />
      <SurvivorshipWarning />

      {/* 範圍預設 — 保守/平衡/激進 */}
      <section className="flex flex-col gap-2">
        <h2 className="text-sm font-semibold text-[var(--text-secondary)] inline-flex items-center gap-1.5">
          <Icon name="palette" size={16} className="text-[var(--brand-500)]" />
          掃描範圍預設
          <span className="text-[11px] text-[var(--text-tertiary)] font-normal ml-1">（一鍵套用 + 直接執行）</span>
        </h2>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          {(Object.entries(SCAN_PRESETS) as [keyof typeof SCAN_PRESETS, typeof SCAN_PRESETS[keyof typeof SCAN_PRESETS]][]).map(([key, p]) => {
            const isActive = arrEq(entries, p.cfg.entries) && arrEq(exits, p.cfg.exits) && arrEq(sls, p.cfg.sls) && arrEq(tps, p.cfg.tps);
            const q = new URLSearchParams({
              run: "1",
              mode,
              source,
              ...(source === "custom" && customIds ? { tickers: customIds } : {}),
              entries: p.cfg.entries.join(","),
              exits: p.cfg.exits.join(","),
              sls: p.cfg.sls.join(","),
              tps: p.cfg.tps.join(","),
            }).toString();
            return (
              <Link
                key={key}
                href={`/grid-search?${q}`}
                className={cn(
                  "rounded-xl border p-4 flex flex-col gap-1.5 transition-colors",
                  isActive && hasRun
                    ? "border-[var(--brand-500)] bg-[var(--brand-tint)]"
                    : "border-[var(--border-default)] bg-surface hover:border-[var(--brand-300)]",
                )}
              >
                <span className="inline-flex items-center gap-1.5 text-sm font-semibold">
                  <Icon name={p.icon} size={16} filled={isActive && hasRun} className="text-[var(--brand-500)]" />
                  {p.label}
                  <span className="numeric text-[11px] text-[var(--text-tertiary)] ml-auto">{p.combos} 組</span>
                </span>
                <span className="text-xs text-[var(--text-tertiary)]">{p.desc}</span>
                <span className="text-[11px] text-[var(--text-tertiary)] numeric leading-tight">
                  進 [{p.cfg.entries.join("/")}]・出 [{p.cfg.exits.join("/")}]
                </span>
              </Link>
            );
          })}
        </div>
      </section>

      <form method="GET" action="/grid-search" className="rounded-xl border border-[var(--border-default)] bg-surface p-5 flex flex-col gap-4">
        <input type="hidden" name="run" value="1" />

        <div className="flex flex-wrap gap-x-6 gap-y-2 text-sm border-b border-[var(--border-default)] pb-3">
          <label className="inline-flex items-center gap-1.5">
            <input type="radio" name="mode" value="grid" defaultChecked={mode !== "wf"} />
            <span>網格掃描（找最佳）</span>
            <InfoTip term="grid_search" />
          </label>
          <label className="inline-flex items-center gap-1.5 relative">
            <input type="radio" name="mode" value="wf" defaultChecked={mode === "wf"} />
            <span>Walk-Forward（更嚴謹）</span>
            <span className="text-[11px] font-semibold px-1.5 py-0.5 rounded bg-[var(--brand-500)] text-white">推薦 🎯</span>
            <InfoTip term="walk_forward" />
          </label>
        </div>

        <Field label={`股票來源（自選 ${wl.length} / 自訂）`}>
          <div className="flex gap-4 text-sm">
            <label className="inline-flex items-center gap-1.5"><input type="radio" name="source" value="watchlist" defaultChecked={source !== "custom"} />自選股</label>
            <label className="inline-flex items-center gap-1.5"><input type="radio" name="source" value="custom" defaultChecked={source === "custom"} />自訂</label>
          </div>
        </Field>
        {source === "custom" && (
          <Field label="自訂代號（空白或逗號分隔，上限 20）">
            <textarea name="tickers" rows={2} defaultValue={customIds} placeholder="2330 2317 2454" className={cn(inputCls, "font-mono resize-y")} />
          </Field>
        )}

        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
          <Field label={`進場候選（共 ${entries.length} 個）`} term="entry_threshold" hint="多個用逗號分隔，如 60,65,70">
            <input name="entries" defaultValue={entries.join(",")} placeholder="60,65,70" className={inputCls} />
          </Field>
          <Field label={`出場候選（共 ${exits.length} 個）`} term="exit_threshold" hint="例如 35,40">
            <input name="exits" defaultValue={exits.join(",")} placeholder="35,40" className={inputCls} />
          </Field>
          <Field label={`停損候選（共 ${sls.length} 個）`} term="stop_loss" hint="小數格式，0.08 代表 -8%">
            <input name="sls" defaultValue={sls.join(",")} placeholder="0.08,0.10" className={inputCls} />
          </Field>
          <Field label={`停利候選（共 ${tps.length} 個）`} term="take_profit" hint="小數格式，0.20 代表 +20%">
            <input name="tps" defaultValue={tps.join(",")} placeholder="0.15,0.20" className={inputCls} />
          </Field>
          <Field
            label={`動態停利 K（共 ${tpKs.length} 個，留空 = 關閉）`}
            hint="Chandelier ATR 倍數；2.5–3.5 為實證最佳區間。非空時 mode=both，會跟固定停利併存「先觸發先出」"
          >
            <input name="tpKs" defaultValue={tpKs.join(",")} placeholder="2.5,3.0,3.5" className={inputCls} />
          </Field>
          <Field label={`最長持有：${maxHold} 天`} term="max_hold">
            <input name="maxHold" type="range" min={10} max={120} step={5} defaultValue={maxHold} className={rangeCls} />
          </Field>
          {mode === "grid" ? (
            <Field label={`回測天數：${lookback}`} term="lookback_days">
              <input name="lookback" type="range" min={100} max={900} step={50} defaultValue={lookback} className={rangeCls} />
            </Field>
          ) : (
            <>
              <Field label={`時間切段數：${num(sp.splits, 3)}`} term="n_splits">
                <input name="splits" type="range" min={2} max={5} step={1} defaultValue={num(sp.splits, 3)} className={rangeCls} />
              </Field>
              <Field label={`Train 比例：${num(sp.train, 0.7)}`} term="train_ratio">
                <input name="train" type="range" min={0.5} max={0.9} step={0.05} defaultValue={num(sp.train, 0.7)} className={rangeCls} />
              </Field>
            </>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <button type="submit" className={btnPrimary}>
            <Icon name="science" size={16} />執行回測
          </button>
          <Link href="/grid-search" className={btnSecondary}>
            <Icon name="refresh" size={16} />重設
          </Link>
          <div className="ml-auto flex flex-col items-end gap-1 text-xs">
            <div className="inline-flex items-center gap-1.5">
              <span className="text-[var(--text-tertiary)]">組合數</span>
              <span className="numeric font-semibold">{combos}</span>
              <span className="text-[11px] text-[var(--text-tertiary)]">/ 80</span>
              <span className="text-[11px] text-[var(--text-tertiary)] ml-1">
                × {stockIds.length || (source === "watchlist" ? wl.length : "?")} 檔
              </span>
            </div>
            <div className="w-48 h-2 rounded-full bg-subtle overflow-hidden">
              <div
                className={cn(
                  "h-full transition-all",
                  combos > 80 ? "bg-[var(--color-down)]"
                  : combos > 40 ? "bg-[var(--warning-fg)]"
                  : "bg-[var(--up-500)]",
                )}
                style={{ width: `${Math.min(100, (combos / 80) * 100)}%` }}
              />
            </div>
            {combos > 80 && <span className="text-[var(--color-down)] font-medium">超過 80 會被拒，試試「保守」預設</span>}
          </div>
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
      ) : mode === "grid" && gridResult ? (
        <>
          <GridResultBlock result={gridResult} />
          <NextStepCards items={[
            {
              href: gridResult.best
                ? `/portfolio-backtest?run=1&entry=${gridResult.best.entry}&exit=${gridResult.best.exit}&sl=${gridResult.best.sl}&tp=${gridResult.best.tp}`
                : "/portfolio-backtest",
              icon: "verified",
              title: "套用最佳參數做投組回測",
              description: "把找到的「最佳組合」實際套用到自選股，看在每檔的表現。",
            },
            {
              href: `/grid-search?mode=wf${sp.tickers ? `&tickers=${sp.tickers}` : ""}`,
              icon: "shield",
              title: "用 Walk-Forward 再驗證",
              description: "歷史最佳不等於未來最佳。Walk-Forward 會檢查這組參數能不能在新時段延續。",
            },
          ]} />
        </>
      ) : mode === "wf" && wfResult ? (
        <>
          <WalkForwardBlock result={wfResult} />
          <NextStepCards items={[
            {
              href: "/portfolio-backtest",
              icon: "verified",
              title: "套用測試期最佳參數",
              description: "看哪段測試期最賺、選那組參數套到投組回測再驗一次。",
            },
            {
              href: "/weight-tuner",
              icon: "tune",
              title: "調評分權重看看",
              description: "如果參數怎麼調都不賺，可能是評分本身偏離你的策略；試試調整權重。",
            },
          ]} />
        </>
      ) : null}
    </div>
  );
}

function GridResultBlock({ result }: { result: GridSearchResponse }) {
  // 任一組有開動態停利就秀 K 欄；全部沒開就隱藏避免畫面噪音
  const hasK = result.rows.some((r) => r.trailingTpK != null);
  return (
    <>
      <section className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <KPIStat label="掃描組合" value={`${result.combos}`} footnote={`耗時 ${result.elapsedSec}s`} tone="neutral" />
        <KPIStat label="最佳 Alpha" value={result.best ? fmtPct(result.best.avgAlpha, 2) : "—"} tone={tone(result.best?.avgAlpha ?? null)} term="alpha" />
        <KPIStat label="最佳策略報酬" value={result.best ? fmtPct(result.best.avgTotalReturn, 2) : "—"} tone={tone(result.best?.avgTotalReturn ?? null)} />
        <KPIStat label="最佳勝率" value={result.best ? fmtPct(result.best.overallWinrate, 1) : "—"} tone={(result.best?.overallWinrate ?? 0) >= 0.5 ? "up" : "down"} term="win_rate" />
      </section>

      {result.best && (
        <div className="rounded-xl border border-[var(--info-border)] bg-[var(--info-bg)] p-4 text-sm text-[var(--info-fg)]">
          <div className="font-semibold inline-flex items-center gap-2">
            <Icon name="emoji_events" size={16} filled />最佳組合
          </div>
          <div className="numeric mt-1">
            entry={result.best.entry}、exit={result.best.exit}、sl={(result.best.sl*100).toFixed(1)}%、tp={(result.best.tp*100).toFixed(1)}%
            {result.best.trailingTpK != null && <>、trailing K={result.best.trailingTpK.toFixed(1)}</>}
          </div>
        </div>
      )}

      <section className="flex flex-col gap-3">
        <h2 className="text-base font-semibold">所有組合（依 平均 Alpha 降序）</h2>
        <TableContainer>
          <table className="w-full text-[15px] min-w-[760px]">
            <thead className="bg-subtle">
              <tr>
                <Th align="right">Entry</Th><Th align="right">Exit</Th><Th align="right">SL</Th><Th align="right">TP</Th>
                {hasK && <Th align="right">Trailing K</Th>}
                <Th align="right">平均 Alpha</Th><Th align="right">平均報酬</Th><Th align="right">勝率</Th><Th align="right">交易總數</Th>
              </tr>
            </thead>
            <tbody>
              {result.rows.map((r, i) => (
                <tr key={i} className={cn("border-t border-[var(--border-default)] hover:bg-subtle", i === 0 && "bg-[var(--info-bg)]/50")}>
                  <Td align="right" numeric>{r.entry}</Td>
                  <Td align="right" numeric>{r.exit}</Td>
                  <Td align="right" numeric>{(r.sl*100).toFixed(1)}%</Td>
                  <Td align="right" numeric>{(r.tp*100).toFixed(1)}%</Td>
                  {hasK && (
                    <Td align="right" numeric>
                      {r.trailingTpK != null
                        ? r.trailingTpK.toFixed(1)
                        : <span className="text-[var(--text-tertiary)]">—</span>}
                    </Td>
                  )}
                  <Td align="right" numeric><span className={cn("font-semibold", tc(r.avgAlpha))}>{fmtPct(r.avgAlpha, 2)}</span></Td>
                  <Td align="right" numeric><span className={tc(r.avgTotalReturn)}>{fmtPct(r.avgTotalReturn, 2)}</span></Td>
                  <Td align="right" numeric>{fmtPct(r.overallWinrate, 1)}</Td>
                  <Td align="right" numeric>{r.nTradesTotal}</Td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableContainer>
      </section>
    </>
  );
}

function WalkForwardBlock({ result }: { result: WalkForwardResponse }) {
  if (result.note) {
    return (
      <div className="rounded-xl border border-[var(--warning-border)] bg-[var(--warning-bg)] p-5 text-[var(--warning-fg)]">
        {result.note}
      </div>
    );
  }
  return (
    <>
      <section className="grid grid-cols-2 md:grid-cols-3 gap-3">
        <KPIStat label="訓練期平均報酬" value={fmtPct(result.avgTrainReturn, 2)} tone={tone(result.avgTrainReturn)} footnote="從歷史挖出最佳參數" />
        <KPIStat label="測試期平均報酬" value={fmtPct(result.avgTestReturn, 2)} tone={tone(result.avgTestReturn)} footnote="實戰才看這個" term="overfit" />
        <KPIStat label="切段數" value={`${result.splits.length}`} tone="neutral" term="n_splits" />
      </section>

      {result.overfitWarning && (
        <div className="rounded-xl border border-[var(--warning-border)] bg-[var(--warning-bg)] p-4 text-sm text-[var(--warning-fg)] inline-flex items-start gap-2">
          <Icon name="warning" size={18} filled />
          <div>
            <div className="font-semibold">疑似過擬合 ⚠️</div>
            <div className="text-xs mt-1 leading-relaxed">
              訓練期 {fmtPct(result.avgTrainReturn, 2)} ≫ 測試期 {fmtPct(result.avgTestReturn, 2)}（或測試期為負）。
              代表這組參數只在「事後看」漂亮、放到新資料就失靈。實際操作要謹慎，可考慮放寬參數網格或拉長 lookback 看是否穩定。
            </div>
          </div>
        </div>
      )}

      <TableContainer>
        <table className="w-full text-[15px] min-w-[800px]">
          <thead className="bg-subtle">
            <tr>
              <Th align="right">段</Th>
              <Th>Train 期間</Th>
              <Th>Test 期間</Th>
              <Th align="right">最佳 Entry</Th>
              <Th align="right">最佳 Exit</Th>
              <Th align="right">Train 報酬</Th>
              <Th align="right">Test 報酬</Th>
              <Th align="right">Test Alpha vs 0050</Th>
              <Th align="right">Test 交易數</Th>
            </tr>
          </thead>
          <tbody>
            {result.splits.map((s) => (
              <tr key={s.split} className="border-t border-[var(--border-default)] hover:bg-subtle">
                <Td align="right" numeric>#{s.split}</Td>
                <Td numeric className="text-xs">{s.trainPeriod}</Td>
                <Td numeric className="text-xs">{s.testPeriod}</Td>
                <Td align="right" numeric>{s.bestEntry ?? "—"}</Td>
                <Td align="right" numeric>{s.bestExit ?? "—"}</Td>
                <Td align="right" numeric><span className={tc(s.trainReturn)}>{fmtPct(s.trainReturn, 2)}</span></Td>
                <Td align="right" numeric><span className={cn("font-semibold", tc(s.testReturn))}>{fmtPct(s.testReturn, 2)}</span></Td>
                <Td align="right" numeric><span className={tc(s.testAlpha0050)}>{fmtPct(s.testAlpha0050, 2)}</span></Td>
                <Td align="right" numeric>{s.testNTrades}</Td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableContainer>
    </>
  );
}

