import {
  apiGet,
  apiGetOptional,
  type FactorICResponse,
  type SubFactorICResponse,
  type RollingICResponse,
} from "@/lib/api";
import { PageHeader } from "@/components/primitives/PageHeader";
import { BackendDownError } from "@/components/primitives/BackendDownError";
import { EmptyState } from "@/components/primitives/EmptyState";
import { Th, Td } from "@/components/primitives/Table";
import { TableContainer } from "@/components/primitives/TableContainer";
import { RollingICChart } from "@/components/charts/RollingICChart";
import { NextStepCards } from "@/components/primitives/NextStepCard";

// 6-week TTL：IC 算一次 ~5-30s，沒必要每次重算（資料每天才寫一次）。
// 真要強制重算就 ?lookback=N，會 bypass cache。
export const revalidate = 21600;

const FACTOR_LABEL: Record<string, string> = {
  short: "短期分數",
  mid: "中期分數",
  long: "長期分數",
  composite: "綜合分",
  vr_macd: "VR 量能因子",
};

const FACTORS = ["short", "mid", "long", "composite", "vr_macd"] as const;

// 子因子中文標籤（短/中/長 各 horizon 內部子分數）
const SUBFACTOR_LABEL: Record<string, string> = {
  // short
  ma_alignment: "MA 排列",
  kd: "KD",
  macd: "MACD",
  rsi: "RSI",
  bollinger: "布林通道",
  volume: "量能",
  // 保留 key 名稱 vr_macd 以維持 API 相容，語意已改為純 VR。
  vr_macd: "VR",
  foreign: "外資 (短)",
  trust: "投信 (短)",
  margin_change: "融資變動",
  // mid
  trend: "趨勢方向",
  foreign_cum: "外資 20 日累計",
  trust_cum: "投信 20 日累計",
  eps_growth: "EPS YoY",
  revenue_growth: "營收 YoY",
  // long
  roe: "ROE",
  margin_quality: "毛/淨利率品質",
  eps_cagr_3y: "EPS 3 年 CAGR",
  dividend: "殖利率",
  valuation: "估值 (PER/PBR)",
};

const HORIZON_LABEL: Record<string, string> = {
  short: "短期分數",
  mid: "中期分數",
  long: "長期分數",
};

type Cell = {
  ic: number | null;
  icIr: number | null;
  spread: number | null;
  nDates: number;
  avgN: number;
};

function ciSpansZero(lo: number | null | undefined, hi: number | null | undefined) {
  return lo != null && hi != null && lo <= 0 && hi >= 0;
}

function ICColor({ ic, muted = false }: { ic: number | null; muted?: boolean }) {
  if (ic == null) return null;
  // 紅漲綠跌：正 IC = 預測力佳 = 紅；負 IC = 反向 = 綠
  // 強度分三級：|ic| < 0.05 灰、0.05–0.10 中、> 0.10 強
  const abs = Math.abs(ic);
  const positive = ic > 0;
  const intensity =
    abs < 0.05 ? "weak" :
    abs < 0.10 ? "mid" :
    "strong";
  const cls =
    intensity === "weak"
      ? "bg-subtle text-[var(--text-tertiary)]"
      : intensity === "mid"
        ? positive
          ? "bg-[var(--color-up-bg)] text-[var(--color-up)]"
          : "bg-[var(--color-down-bg)] text-[var(--color-down)]"
        : positive
          ? "bg-[var(--color-up-bg)] text-[var(--color-up)] font-bold border border-[var(--color-up-border)]"
          : "bg-[var(--color-down-bg)] text-[var(--color-down)] font-bold border border-[var(--color-down-border)]";
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded numeric ${cls} ${muted ? "opacity-55" : ""}`}>
      {ic >= 0 ? "+" : ""}{ic.toFixed(3)}
    </span>
  );
}

export default async function DiagnosticsPage() {
  let data: FactorICResponse;
  let subData: SubFactorICResponse | null = null;
  let rollingData: RollingICResponse | null = null;
  try {
    // IC 計算每次 ~3-5 秒（cross-sectional Spearman × 5 factors × 3 horizons），
    // 但結果只在 daily-update 寫新 snapshot 時才會變 → revalidate 1 小時夠用，
    // 重整想看新算法可以 Ctrl+F5 強迫繞過。
    const results = await Promise.all([
      apiGet<FactorICResponse>("/api/diagnostics/factor-ic", { revalidate: 3600 }),
      // sub-factor 表可能還沒寫（舊 schema 或未跑新 backfill）→ 容錯
      apiGetOptional<SubFactorICResponse>("/api/diagnostics/sub-factor-ic", { revalidate: 3600 }),
      // rolling IC：horizon=20、window=30、lookback 拉到 1500 天用全部資料
      apiGetOptional<RollingICResponse>("/api/diagnostics/rolling-ic?horizon=20&window=30&lookback_days=1500", { revalidate: 3600 }),
    ]);
    data = results[0];
    subData = results[1];
    rollingData = results[2];
  } catch (e) {
    return <BackendDownError error={e} pageTitle="因子檢定" />;
  }

  // group rows by factor → horizon for table
  const byFactorHorizon: Record<string, Record<number, Cell>> = {};
  for (const r of data.rows) {
    const bucket = byFactorHorizon[r.factor] ?? (byFactorHorizon[r.factor] = {});
    const spread =
      r.topQuintileReturn != null && r.botQuintileReturn != null
        ? r.topQuintileReturn - r.botQuintileReturn
        : null;
    bucket[r.horizon] = {
      ic: r.ic,
      icIr: r.icIr,
      spread,
      nDates: r.nDates,
      avgN: r.avgNStocks,
    };
  }

  // 是否完全沒資料 → 顯示提示而非空表
  const allEmpty = data.rows.every((r) => r.ic == null);

  return (
    <div className="p-4 lg:p-8 flex flex-col gap-6 max-w-[1400px] mx-auto">
      <PageHeader
        title="因子檢定"
        icon="insights"
        description="對 signal_history 的歷史快照算 forward-return Information Coefficient（Spearman），檢驗每個分數對 5 / 20 / 60 日後的報酬率有沒有預測力。"
        extra={`回看視窗 ${data.lookbackDays} 天 · 紅 = 正向預測（IC > 0）· 綠 = 反向（IC < 0）· 灰 = 無訊號`}
      />
      <section className="rounded-xl border border-[var(--info-border)] bg-[var(--info-bg)] p-3 text-xs text-[var(--info-fg)] leading-relaxed">
        <strong>檢定假設：</strong>{data.executionAssumption}
        <span className="ml-2">CI 方法：{data.icCiMethod}</span>
      </section>

      {allEmpty ? (
        <EmptyState>
          <div className="flex flex-col gap-2 items-start text-left max-w-xl">
            <span className="font-semibold text-[var(--text-primary)]">尚無足夠歷史快照</span>
            <span className="text-xs text-[var(--text-tertiary)]">
              IC 計算需要至少 5 個 as_of 日期 + 對應的 forward return 資料。
              如果 daily-update 才剛開始跑、或 signal_history 被 prune 過，先補一段歷史：
            </span>
            <code className="text-xs px-2 py-1 rounded bg-subtle font-mono">
              python -m scripts.backfill_signal_history --days 60
            </code>
            <span className="text-xs text-[var(--text-tertiary)]">
              60 天約跑 30 分鐘（每天 score_all 一次）。跑完後本頁的所有 cell 會自動填上。
            </span>
          </div>
        </EmptyState>
      ) : (
        <>
          {/* IC heatmap 表 */}
          <section className="flex flex-col gap-3">
            <h2 className="text-sm font-semibold text-[var(--text-primary)]">IC heatmap（mean Spearman across dates）</h2>
            <TableContainer>
              <table className="w-full text-[15px] min-w-[700px]">
                <thead className="bg-subtle">
                  <tr>
                    <Th sticky className="w-[180px]">因子</Th>
                    {data.horizons.map((h) => (
                      <Th key={h} align="center" className="w-[140px]">{h} 日 forward IC</Th>
                    ))}
                    <Th align="right" className="w-[100px]">樣本日</Th>
                    <Th align="right" className="w-[100px]">avg N</Th>
                  </tr>
                </thead>
                <tbody>
                  {FACTORS.map((f) => {
                    const row = byFactorHorizon[f] ?? {};
                    const firstHasData = data.horizons.find((h) => row[h]?.nDates) ?? data.horizons[0];
                    const ref = firstHasData != null ? row[firstHasData] : undefined;
                    return (
                      <tr key={f} className="border-t border-[var(--border-default)]">
                        <Td sticky>
                          <div className="flex flex-col gap-0.5">
                            <span className="font-medium text-[var(--text-primary)]">{FACTOR_LABEL[f] ?? f}</span>
                            <span className="text-[11px] text-[var(--text-tertiary)] font-mono">{f}</span>
                          </div>
                        </Td>
                        {data.horizons.map((h) => {
                          const c = row[h];
                          return (
                            <Td key={h} align="center">
                              {c?.ic != null ? <ICColor ic={c.ic} /> : <span className="text-[var(--text-tertiary)]">—</span>}
                            </Td>
                          );
                        })}
                        <Td align="right" numeric>{ref?.nDates ?? 0}</Td>
                        <Td align="right" numeric>{ref ? ref.avgN.toFixed(0) : "—"}</Td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </TableContainer>
          </section>

          {/* 細項：IC + 95% bootstrap CI + IC_IR + quintile spread */}
          <section className="flex flex-col gap-3">
            <h2 className="text-sm font-semibold text-[var(--text-primary)]">
              穩定度（IC_IR + 95% CI）與 Q5−Q1 spread
            </h2>
            <TableContainer>
              <table className="w-full text-[15px] min-w-[940px]">
                <thead className="bg-subtle">
                  <tr>
                    <Th sticky className="w-[180px]">因子</Th>
                    <Th align="center" className="w-[80px]">horizon</Th>
                    <Th align="right" className="w-[90px]">IC</Th>
                    <Th align="center" className="w-[160px]">95% CI</Th>
                    <Th align="right" className="w-[80px]">IC_IR</Th>
                    <Th align="right" className="w-[120px]">Q5 報酬</Th>
                    <Th align="right" className="w-[120px]">Q1 報酬</Th>
                    <Th align="right" className="w-[120px]">Q5 − Q1</Th>
                  </tr>
                </thead>
                <tbody>
                  {data.rows.map((r) => {
                    const spread =
                      r.topQuintileReturn != null && r.botQuintileReturn != null
                        ? r.topQuintileReturn - r.botQuintileReturn
                        : null;
                    // 顯著性：CI 不跨 0 才算顯著
                    const ciCrossZero = ciSpansZero(r.icCiLo, r.icCiHi);
                    const icCellCls = r.ic == null
                      ? ""
                      : ciCrossZero
                        ? "text-[var(--text-tertiary)]"  // CI 跨 0 → 統計上跟噪音區分不開，淡化顯示
                        : r.ic > 0
                          ? "text-[var(--color-up)] font-semibold"
                          : "text-[var(--color-down)] font-semibold";
                    return (
                      <tr key={`${r.factor}-${r.horizon}`} className="border-t border-[var(--border-default)]">
                        <Td sticky>
                          <span className="font-medium">{FACTOR_LABEL[r.factor] ?? r.factor}</span>
                        </Td>
                        <Td align="center" numeric>{r.horizon} 日</Td>
                        <Td align="right" numeric>
                          <span className={icCellCls}>
                            {r.ic == null ? "—" : `${r.ic >= 0 ? "+" : ""}${r.ic.toFixed(3)}`}
                          </span>
                        </Td>
                        <Td align="center" numeric>
                          {r.icCiLo == null || r.icCiHi == null ? (
                            <span className="text-[var(--text-tertiary)]">—</span>
                          ) : (
                            <span className={ciCrossZero ? "text-[var(--text-tertiary)]" : "text-[var(--text-secondary)]"} title={ciCrossZero ? "CI 跨 0 → 不顯著（與隨機難區分）" : "CI 不跨 0 → 顯著"}>
                              [{r.icCiLo >= 0 ? "+" : ""}{r.icCiLo.toFixed(3)}, {r.icCiHi >= 0 ? "+" : ""}{r.icCiHi.toFixed(3)}]
                            </span>
                          )}
                        </Td>
                        <Td align="right" numeric>
                          {r.icIr == null ? "—" : `${r.icIr >= 0 ? "+" : ""}${r.icIr.toFixed(2)}`}
                        </Td>
                        <Td align="right" numeric>
                          {r.topQuintileReturn == null ? "—" : `${(r.topQuintileReturn * 100).toFixed(2)}%`}
                        </Td>
                        <Td align="right" numeric>
                          {r.botQuintileReturn == null ? "—" : `${(r.botQuintileReturn * 100).toFixed(2)}%`}
                        </Td>
                        <Td align="right" numeric>
                          {spread == null ? (
                            "—"
                          ) : (
                            <span className={spread > 0 ? "text-[var(--color-up)]" : "text-[var(--color-down)]"}>
                              {spread >= 0 ? "+" : ""}{(spread * 100).toFixed(2)}%
                            </span>
                          )}
                        </Td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </TableContainer>
          </section>

          {/* Rolling IC：跨時間看每個 factor 的 IC 演進，揪 regime artifact */}
          {rollingData && rollingData.rows.length > 0 ? (
            <section className="flex flex-col gap-3">
              <div className="flex flex-col gap-1">
                <h2 className="text-sm font-semibold text-[var(--text-primary)] inline-flex items-center gap-2">
                  Rolling IC 趨勢（{rollingData.window} 日 rolling × {rollingData.horizon} 日 forward）
                </h2>
                <span className="text-[11px] text-[var(--text-tertiary)]">
                  揪 regime artifact 用：mean IC = +0.04 但折線跨期符號翻轉，代表是兩個 regime 平均出來的、不是穩定 alpha
                </span>
              </div>
              <div className="rounded-xl border border-[var(--border-default)] bg-surface p-4">
                <RollingICChart data={rollingData.rows} height={320} />
                <div className="mt-2 text-[11px] text-[var(--text-tertiary)]">
                  Y 軸 = cross-sectional Spearman IC 的 {rollingData.window} 日滾動平均。
                  虛線 = ±0.05（IC 顯著閾值）。0 線上下擺盪 = regime-dependent 因子；單向遠離 0 = 持續訊號。
                  參數：lookback {rollingData.lookbackDays} 日、forward {rollingData.horizon} 日、window {rollingData.window} 日。
                </div>
              </div>
            </section>
          ) : null}

          {/* 子因子 IC 拆解：當「短期 IC ≈ 0」時揪出哪個子分數拖累 */}
          {subData && subData.rows.length > 0 && subData.rows.some((r) => r.ic != null) ? (
            <SubFactorSection subData={subData} />
          ) : subData ? (
            <p className="text-xs text-[var(--text-tertiary)]">
              子因子分數歷史尚未寫入。改了 scoring engine 後跑
              <code className="font-mono mx-1 px-1 rounded bg-subtle">python -m scripts.backfill_signal_history --days 60 --clear</code>
              便會同時填上 signal_history 與 sub-factor parts 兩張表。
            </p>
          ) : null}

          <p className="text-xs text-[var(--text-tertiary)] leading-relaxed">
            <strong>怎麼讀：</strong>
            IC 反映每個交易日的 cross-sectional 排序對齊度，平均後在 ±0.1 之間都很正常；
            台股訊號 |IC| &gt; 0.05 通常算有預測力，&gt; 0.10 算強。
            <strong className="text-[var(--text-primary)]"> 95% CI 跨 0 的 IC 值會淡化顯示</strong>
            — 那種訊號統計上跟隨機難區分，調權重前最好等更多樣本。
            IC_IR &gt; 0.5 代表跨期穩定；&lt; 0.3 代表時好時壞要小心過擬合。
            Q5−Q1 spread 是「買最強 20%、賣最弱 20%」的多空組合在該 horizon 的平均報酬。
            樣本不足（單日 &lt; 30 檔 / 全期 &lt; 5 個 IC 點）會回 — 而非假數字。
            CI 採 Newey-West HAC（lag ≈ horizon−1）修正重疊持有期自相關，避免傳統 naive bootstrap 過度樂觀。
          </p>
          <NextStepCards items={[
            {
              href: "/weight-tuner",
              icon: "tune",
              title: "依因子證據調整權重",
              description: "先看顯著且穩定的因子，再到權重頁做精調。",
            },
            {
              href: "/portfolio-backtest",
              icon: "verified",
              title: "驗證調整後實戰績效",
              description: "用投組回測檢查報酬、回撤與勝率是否同步改善。",
            },
            {
              href: "/lab",
              icon: "science",
              title: "回測工具室完整流程",
              description: "從策略、參數到權重，完整跑完研究閉環。",
            },
          ]} />
        </>
      )}
    </div>
  );
}


function SubFactorSection({ subData }: { subData: SubFactorICResponse }) {
  // 依 horizon 分群，每個 horizon 一張表（rows=factor, cols=forward_horizon）
  const horizons: Array<"short" | "mid" | "long"> = ["short", "mid", "long"];
  // 同 horizon 內保留 factor 第一次出現的順序（與 rubric.py 對齊）
  const factorsByHorizon: Record<string, string[]> = {};
  // (horizon, factor, forward_horizon) → row
  const cellMap: Record<string, Record<string, Record<number, typeof subData.rows[number]>>> = {};
  for (const r of subData.rows) {
    const factorList = factorsByHorizon[r.horizon] ?? (factorsByHorizon[r.horizon] = []);
    if (!factorList.includes(r.factor)) factorList.push(r.factor);
    const horizonBucket = cellMap[r.horizon] ?? (cellMap[r.horizon] = {});
    const factorBucket = horizonBucket[r.factor] ?? (horizonBucket[r.factor] = {});
    factorBucket[r.forwardHorizon] = r;
  }

  return (
    <section className="flex flex-col gap-4">
      <div className="flex flex-col gap-1">
        <h2 className="text-sm font-semibold text-[var(--text-primary)] inline-flex items-center gap-2">
          子因子 IC 拆解
          <span className="text-[11px] font-normal text-[var(--text-tertiary)]">
            回看視窗 {subData.lookbackDays} 天 · 每張表 = 該 horizon 內每個子分數對 5/20/60 日後報酬的 IC
          </span>
        </h2>
        <span className="text-[11px] text-[var(--text-tertiary)]">
          當「短期分數整體 IC ≈ 0」時，這裡能看出是哪幾個子因子互相抵銷
        </span>
        <span className="text-[11px] text-[var(--text-tertiary)]">
          淡化色塊 = 95% CI 跨 0（統計上與噪音難區分）。
        </span>
      </div>

      {horizons.map((h) => {
        const factors = factorsByHorizon[h] ?? [];
        if (factors.length === 0) return null;
        return (
          <div key={h} className="flex flex-col gap-2">
            <h3 className="text-xs font-semibold text-[var(--text-secondary)]">
              {HORIZON_LABEL[h] ?? h}（{factors.length} 個子因子）
            </h3>
            <TableContainer>
              <table className="w-full text-[14px] min-w-[640px]">
                <thead className="bg-subtle">
                  <tr>
                    <Th sticky className="w-[180px]">子因子</Th>
                    {subData.horizons.map((fh) => (
                      <Th key={fh} align="center" className="w-[120px]">{fh} 日 IC</Th>
                    ))}
                    <Th align="right" className="w-[90px]">樣本日</Th>
                  </tr>
                </thead>
                <tbody>
                  {factors.map((f) => {
                    const factorCells = cellMap[h]?.[f] ?? {};
                    const refCell = subData.horizons.map((fh) => factorCells[fh]).find((c) => c?.nDates) ?? Object.values(factorCells)[0];
                    return (
                      <tr key={f} className="border-t border-[var(--border-default)]">
                        <Td sticky>
                          <div className="flex flex-col gap-0.5">
                            <span className="font-medium text-[var(--text-primary)]">{SUBFACTOR_LABEL[f] ?? f}</span>
                            <span className="text-[11px] text-[var(--text-tertiary)] font-mono">{f}</span>
                          </div>
                        </Td>
                        {subData.horizons.map((fh) => {
                          const c = factorCells[fh];
                          const muted = ciSpansZero(c?.icCiLo, c?.icCiHi);
                          return (
                            <Td key={fh} align="center">
                              {c?.ic != null ? (
                                <ICColor
                                  ic={c.ic}
                                  muted={muted}
                                />
                              ) : <span className="text-[var(--text-tertiary)]">—</span>}
                            </Td>
                          );
                        })}
                        <Td align="right" numeric>{refCell?.nDates ?? 0}</Td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </TableContainer>
          </div>
        );
      })}
    </section>
  );
}
