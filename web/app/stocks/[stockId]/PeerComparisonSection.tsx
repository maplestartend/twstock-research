/**
 * 個股 vs 同業中位數比較區塊。
 *
 * 為什麼顯示「rank / outOf」而非 percentile：
 * - 樣本小（5~30 檔）的產業，算 percentile 反而失真（一檔差就少 20%）
 * - 排名是直觀的「N 檔中第幾名」，使用者不必心算
 *
 * 為什麼用 horizontal bar 而非 scatter：
 * - 一個資料點 vs 一條中位數線，scatter 兩個點易誤讀
 * - bar 把 value / median 的相對位置以長度直接表達，配 rank 文字 = 雙重編碼
 */
import { apiGetOptional, type PeerComparison } from "@/lib/api";
import { Icon } from "@/components/primitives/Icon";
import { SectionTitle } from "@/components/primitives/SectionTitle";
import { cn } from "@/lib/utils";

export async function PeerComparisonSection({ stockId }: { stockId: string }) {
  // ETF / 興櫃 / 樣本不足 → API 回 404；用 Optional 變 null 後整段不渲染。
  // 同業中位數是 day-stable 資料 → 15 分鐘 ISR + snapshot tag（手動重算/restart 失效），
  // 不必每次進個股頁都重算同業比較（父頁已不再 force-dynamic）。
  const peers = await apiGetOptional<PeerComparison>(
    `/api/stocks/${encodeURIComponent(stockId)}/peers`,
    { revalidate: 900, tags: ["snapshot"] },
  );
  if (!peers || peers.metrics.length === 0) return null;

  return (
    <section className="flex flex-col gap-3">
      <SectionTitle icon="groups">
        同業比較 · {peers.industry}（共 {peers.peerCount} 檔）
      </SectionTitle>
      <div className="rounded-xl border border-[var(--border-default)] bg-surface p-5 flex flex-col gap-4">
        {peers.metrics.map((m) => (
          <PeerMetricRow key={m.key} metric={m} />
        ))}
        <p className="text-[11px] text-[var(--text-tertiary)] pt-2 border-t border-[var(--border-default)]/60">
          <Icon name="info" size={11} className="inline-block mr-1 align-text-bottom" />
          中位數計算自同產業上市/櫃個股；ETF 與虧損股 PER 已自動排除。樣本不足 5 檔的指標顯示為「—」。
        </p>
      </div>
    </section>
  );
}

function PeerMetricRow({ metric }: { metric: PeerComparison["metrics"][number] }) {
  const { label, unit, value, median, betterDirection, rank, outOf } = metric;

  // 判定「比中位數好 / 差」：直接用 betterDirection 決定方向
  const isBetter =
    value != null && median != null
      ? betterDirection === "higher"
        ? value > median
        : value < median
      : null;
  const tone = isBetter == null ? "neutral" : isBetter ? "up" : "down";

  // 視覺化長度：以「value 與 median 的較大者」為 100% 基準，較小者按比例。
  // 都 ≤ 0（如 EPS YoY 負成長）時，以絕對值的較大者為基準，bar 仍能傳達「誰差更多」。
  const barBase = (() => {
    if (value == null || median == null) return null;
    const m = Math.max(Math.abs(value), Math.abs(median), 1e-9);
    return { value: value / m, median: median / m, base: m };
  })();

  return (
    <div className="grid grid-cols-1 md:grid-cols-[110px_1fr_auto] gap-x-4 gap-y-1 items-baseline">
      <span className="text-sm font-medium text-[var(--text-secondary)]">{label}</span>

      {/* Value bar */}
      <div className="flex flex-col gap-1.5 min-w-0">
        {barBase ? (
          <BarPair
            valuePct={barBase.value}
            medianPct={barBase.median}
            tone={tone}
          />
        ) : (
          <span className="text-xs text-[var(--text-tertiary)] italic">
            {value == null ? "本檔缺資料" : "同業樣本不足，無法計算中位數"}
          </span>
        )}
        <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-[var(--text-tertiary)]">
          <span>
            本檔
            <span className={cn("ml-1 numeric font-medium", toneClass(tone))}>
              {fmtMetric(value, unit)}
            </span>
          </span>
          <span>
            產業中位數
            <span className="ml-1 numeric font-medium text-[var(--text-secondary)]">
              {fmtMetric(median, unit)}
            </span>
          </span>
        </div>
      </div>

      {/* Rank badge */}
      <div className="flex items-center justify-end">
        {rank != null && outOf > 0 ? (
          <RankBadge rank={rank} outOf={outOf} tone={tone} />
        ) : (
          <span className="text-[11px] text-[var(--text-tertiary)]">—</span>
        )}
      </div>
    </div>
  );
}

function BarPair({
  valuePct,
  medianPct,
  tone,
}: {
  valuePct: number;     // 已歸一化到 [-1, 1]（含負）
  medianPct: number;
  tone: "up" | "down" | "neutral";
}) {
  // 都正：左對齊 0，bar 往右；有負值：用中線分隔，正往右、負往左
  const hasNeg = valuePct < 0 || medianPct < 0;
  const bgValue =
    tone === "up" ? "bg-[var(--color-up)]" :
    tone === "down" ? "bg-[var(--color-down)]" :
    "bg-[var(--text-tertiary)]";
  const bgMedian = "bg-[var(--text-tertiary)]/40";

  if (!hasNeg) {
    return (
      <div className="relative h-5 flex flex-col gap-0.5">
        <div className="relative h-2 bg-subtle rounded">
          <div
            className={cn("absolute left-0 top-0 h-full rounded transition-[width] duration-200", bgValue)}
            style={{ width: `${Math.min(100, valuePct * 100)}%` }}
            aria-label="本檔"
          />
        </div>
        <div className="relative h-2 bg-subtle rounded">
          <div
            className={cn("absolute left-0 top-0 h-full rounded transition-[width] duration-200", bgMedian)}
            style={{ width: `${Math.min(100, medianPct * 100)}%` }}
            aria-label="同業中位數"
          />
        </div>
      </div>
    );
  }

  // 含負值：以中線為 0、左右對稱顯示
  const renderBar = (pct: number, color: string, label: string) => {
    const width = Math.min(50, Math.abs(pct) * 50);
    const isNeg = pct < 0;
    return (
      <div className="relative h-2 bg-subtle rounded">
        <div className="absolute left-1/2 top-0 bottom-0 w-px bg-[var(--border-default)]" />
        <div
          className={cn("absolute top-0 h-full rounded transition-[width] duration-200", color)}
          style={{
            left: isNeg ? `${50 - width}%` : "50%",
            width: `${width}%`,
          }}
          aria-label={label}
        />
      </div>
    );
  };
  return (
    <div className="flex flex-col gap-0.5">
      {renderBar(valuePct, bgValue, "本檔")}
      {renderBar(medianPct, bgMedian, "同業中位數")}
    </div>
  );
}

function RankBadge({
  rank,
  outOf,
  tone,
}: {
  rank: number;
  outOf: number;
  tone: "up" | "down" | "neutral";
}) {
  const cls =
    tone === "up" ? "bg-[var(--color-up-bg)] text-[var(--color-up)] border-[var(--color-up-border)]" :
    tone === "down" ? "bg-[var(--color-down-bg)] text-[var(--color-down)] border-[var(--color-down-border)]" :
    "bg-subtle text-[var(--text-secondary)] border-[var(--border-default)]";
  return (
    <span
      className={cn("inline-flex items-center px-2 py-0.5 rounded border text-xs numeric", cls)}
      title={`同業 ${outOf} 檔中第 ${rank} 名（含本檔）`}
    >
      <span className="font-semibold">#{rank}</span>
      <span className="text-[10px] opacity-75 ml-0.5">/{outOf}</span>
    </span>
  );
}

function toneClass(tone: "up" | "down" | "neutral"): string {
  if (tone === "up") return "text-[var(--color-up)]";
  if (tone === "down") return "text-[var(--color-down)]";
  return "text-[var(--text-secondary)]";
}

function fmtMetric(v: number | null, unit: string): string {
  if (v == null) return "—";
  if (unit === "%") return `${(v * 100).toFixed(2)}%`;
  if (unit === "倍") return `${v.toFixed(2)}×`;
  return v.toFixed(2);
}
