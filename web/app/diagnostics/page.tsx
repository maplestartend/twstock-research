import { apiGet, type FactorICResponse } from "@/lib/api";
import { PageHeader } from "@/components/primitives/PageHeader";
import { BackendDownError } from "@/components/primitives/BackendDownError";
import { EmptyState } from "@/components/primitives/EmptyState";
import { Th, Td } from "@/components/primitives/Table";
import { TableContainer } from "@/components/primitives/TableContainer";

// 6-week TTL：IC 算一次 ~5-30s，沒必要每次重算（資料每天才寫一次）。
// 真要強制重算就 ?lookback=N，會 bypass cache。
export const revalidate = 21600;

const FACTOR_LABEL: Record<string, string> = {
  short: "短期分數",
  mid: "中期分數",
  long: "長期分數",
  composite: "綜合分",
  vr_macd: "VR×MACD 量能動能",
};

const FACTORS = ["short", "mid", "long", "composite", "vr_macd"] as const;

type Cell = {
  ic: number | null;
  icIr: number | null;
  spread: number | null;
  nDates: number;
  avgN: number;
};

function ICColor({ ic }: { ic: number | null }) {
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
    <span className={`inline-flex items-center px-2 py-0.5 rounded numeric ${cls}`}>
      {ic >= 0 ? "+" : ""}{ic.toFixed(3)}
    </span>
  );
}

export default async function DiagnosticsPage() {
  let data: FactorICResponse;
  try {
    // IC 計算每次 ~3-5 秒（cross-sectional Spearman × 5 factors × 3 horizons），
    // 但結果只在 daily-update 寫新 snapshot 時才會變 → revalidate 1 小時夠用，
    // 重整想看新算法可以 Ctrl+F5 強迫繞過。
    data = await apiGet<FactorICResponse>("/api/diagnostics/factor-ic", { revalidate: 3600 });
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

          {/* 細項：IC_IR + quintile spread */}
          <section className="flex flex-col gap-3">
            <h2 className="text-sm font-semibold text-[var(--text-primary)]">穩定度（IC_IR）與 Q5−Q1 spread</h2>
            <TableContainer>
              <table className="w-full text-[15px] min-w-[800px]">
                <thead className="bg-subtle">
                  <tr>
                    <Th sticky className="w-[180px]">因子</Th>
                    <Th align="center" className="w-[100px]">horizon</Th>
                    <Th align="right" className="w-[100px]">IC</Th>
                    <Th align="right" className="w-[100px]">IC_IR</Th>
                    <Th align="right" className="w-[140px]">Q5 平均報酬</Th>
                    <Th align="right" className="w-[140px]">Q1 平均報酬</Th>
                    <Th align="right" className="w-[140px]">Q5 − Q1 spread</Th>
                  </tr>
                </thead>
                <tbody>
                  {data.rows.map((r) => {
                    const spread =
                      r.topQuintileReturn != null && r.botQuintileReturn != null
                        ? r.topQuintileReturn - r.botQuintileReturn
                        : null;
                    return (
                      <tr key={`${r.factor}-${r.horizon}`} className="border-t border-[var(--border-default)]">
                        <Td sticky>
                          <span className="font-medium">{FACTOR_LABEL[r.factor] ?? r.factor}</span>
                        </Td>
                        <Td align="center" numeric>{r.horizon} 日</Td>
                        <Td align="right" numeric>
                          {r.ic == null ? "—" : `${r.ic >= 0 ? "+" : ""}${r.ic.toFixed(3)}`}
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

          <p className="text-xs text-[var(--text-tertiary)] leading-relaxed">
            <strong>怎麼讀：</strong>
            IC 反映每個交易日的 cross-sectional 排序對齊度，平均後在 ±0.1 之間都很正常；
            台股訊號 |IC| &gt; 0.05 通常算有預測力，&gt; 0.10 算強。
            IC_IR &gt; 0.5 代表跨期穩定，可以放心當權重；&lt; 0.3 代表時好時壞，要小心過擬合。
            Q5−Q1 spread 是「買最強 20%、賣最弱 20%」的多空組合在該 horizon 的平均報酬，正值代表因子有區分能力。
            樣本不足（單日 &lt; 30 檔 / 全期 &lt; 5 個 IC 點）會回 — 而非假數字。
          </p>
        </>
      )}
    </div>
  );
}
