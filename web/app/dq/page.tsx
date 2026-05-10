import Link from "next/link";
import { apiGet, type DqSummary, type DataFreshness } from "@/lib/api";
import { Icon } from "@/components/primitives/Icon";
import { PageHeader } from "@/components/primitives/PageHeader";
import { EmptyState } from "@/components/primitives/EmptyState";
import { KPIStat } from "@/components/primitives/KPIStat";
import { DataFreshnessBadge } from "@/components/primitives/DataFreshnessBadge";
import { BackendDownError } from "@/components/primitives/BackendDownError";
import { Th, TdCompact as Td } from "@/components/primitives/Table";
import { TableContainer } from "@/components/primitives/TableContainer";
import { StockIdCell } from "@/components/primitives/StockIdCell";
import { fmtPct } from "@/lib/format";
import { cn } from "@/lib/utils";

export const revalidate = 60;

type Kind = "limit_up" | "limit_down" | "volume_spike" | "stale" | "huge_gap";
type Severity = "critical" | "warning" | "info";

const KIND_META: Record<Kind, { label: string; icon: string }> = {
  limit_up:     { label: "漲停 / 急漲",       icon: "trending_up" },
  limit_down:   { label: "跌停 / 急跌",       icon: "trending_down" },
  volume_spike: { label: "量爆",              icon: "bolt" },
  stale:        { label: "停滯（疑停牌）",    icon: "pause_circle" },
  huge_gap:     { label: "跳空缺口",          icon: "open_in_new" },
};

const SEV_META: Record<Severity, { tone: string; cls: string; label: string }> = {
  critical: { tone: "critical", cls: "bg-[var(--color-down-bg)] text-[var(--color-down)] border-[var(--color-down)]", label: "嚴重" },
  warning:  { tone: "warning",  cls: "bg-[var(--warning-bg)] text-[var(--warning-fg)] border-[var(--warning-border)]", label: "警告" },
  info:     { tone: "info",     cls: "bg-[var(--info-bg)] text-[var(--info-fg)] border-[var(--info-border)]", label: "提醒" },
};

export default async function DqPage({
  searchParams,
}: {
  searchParams: Promise<{ days?: string; kind?: string; sev?: string }>;
}) {
  const sp = await searchParams;
  const days = Math.max(3, Math.min(60, Number(sp.days) || 10));
  const kindFilter = sp.kind ?? "";
  const sevFilter = sp.sev ?? "";

  let dq: DqSummary, freshness: DataFreshness[];
  try {
    [dq, freshness] = await Promise.all([
      apiGet<DqSummary>(`/api/dq/summary?days=${days}`),
      apiGet<DataFreshness[]>("/api/dashboard/data-freshness", { tags: ["snapshot"] }),
    ]);
  } catch (e) {
    return <BackendDownError error={e} pageTitle="資料品質" />;
  }

  const filtered = dq.anomalies.filter((a) =>
    (kindFilter ? a.kind === kindFilter : true) &&
    (sevFilter ? a.severity === sevFilter : true),
  );

  // 各 kind 數量供 chip 顯示
  const kindCounts: Record<Kind, number> = {
    limit_up: 0, limit_down: 0, volume_spike: 0, stale: 0, huge_gap: 0,
  };
  dq.anomalies.forEach((a) => { kindCounts[a.kind as Kind] += 1; });

  const sevCounts: Record<Severity, number> = { critical: 0, warning: 0, info: 0 };
  dq.anomalies.forEach((a) => { sevCounts[a.severity] += 1; });

  return (
    <div className="p-4 lg:p-8 flex flex-col gap-8 max-w-[1600px] mx-auto">
      <PageHeader
        title="資料品質"
        icon="health_and_safety"
        description={`掃描焦點股的價格異常 + 資料缺值，避免拿到髒資料下決策。${dq.scope}`}
        extra={`資料更新：${dq.asOf ?? "—"}　·　窗口：近 ${days} 日`}
      />

      {/* 期間切換 */}
      <section className="flex items-center gap-2 text-sm">
        <span className="text-[var(--text-tertiary)]">窗口</span>
        {[5, 10, 20, 60].map((d) => {
          const active = d === days;
          return (
            <Link
              key={d}
              href={`/dq?days=${d}${kindFilter ? `&kind=${kindFilter}` : ""}${sevFilter ? `&sev=${sevFilter}` : ""}`}
              className={cn(
                "numeric px-2.5 py-1 rounded border text-xs",
                active
                  ? "bg-[var(--brand-500)] text-white border-[var(--brand-500)]"
                  : "bg-surface text-[var(--text-tertiary)] border-[var(--border-default)] hover:border-[var(--brand-300)]",
              )}
            >
              近 {d} 日
            </Link>
          );
        })}
      </section>

      {/* KPI */}
      <section className="grid grid-cols-2 lg:grid-cols-5 gap-3">
        <KPIStat label="總異常" value={String(dq.nAnomalies)} tone={dq.nAnomalies > 0 ? "down" : "up"} />
        <KPIStat label="嚴重" value={String(sevCounts.critical)} tone={sevCounts.critical > 0 ? "down" : "up"} />
        <KPIStat label="警告" value={String(sevCounts.warning)} tone="neutral" />
        <KPIStat label="提醒" value={String(sevCounts.info)} tone="neutral" />
        <KPIStat label="資料缺值" value={String(dq.nGaps)} tone={dq.nGaps > 0 ? "down" : "up"} footnote={`期望 ${days} 日`} />
      </section>

      {/* 表級新鮮度 */}
      <section className="flex flex-col gap-3">
        <h2 className="text-base font-semibold inline-flex items-center gap-2">
          <Icon name="manage_history" size={20} className="text-[var(--brand-500)]" />
          各表新鮮度
        </h2>
        <div className="rounded-xl border border-[var(--border-default)] bg-surface p-4 flex flex-wrap gap-3">
          {freshness.map((f) => (
            <div key={f.table} className="flex flex-col gap-1">
              <span className="text-xs text-[var(--text-tertiary)]">{f.label}</span>
              <DataFreshnessBadge tone={f.tone} latestDate={f.latestDate} lagDays={f.lagDays} />
            </div>
          ))}
        </div>
      </section>

      {/* 篩選器 */}
      {dq.anomalies.length > 0 && (
        <section className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-xs">
            <div className="flex items-center gap-2">
              <span className="text-[var(--text-tertiary)]">嚴重度</span>
              {(["critical", "warning", "info"] as const).map((sv) => {
                const active = sevFilter === sv;
                return (
                  <Link
                    key={sv}
                    href={`/dq?days=${days}${kindFilter ? `&kind=${kindFilter}` : ""}${active ? "" : `&sev=${sv}`}`}
                    className={cn(
                      "px-2 py-0.5 rounded border",
                      active
                        ? SEV_META[sv].cls + " font-semibold"
                        : "bg-surface text-[var(--text-tertiary)] border-[var(--border-default)] hover:border-[var(--brand-300)]",
                    )}
                  >
                    {SEV_META[sv].label} ({sevCounts[sv]})
                  </Link>
                );
              })}
              {sevFilter && (
                <Link href={`/dq?days=${days}${kindFilter ? `&kind=${kindFilter}` : ""}`} className="text-[var(--text-tertiary)] hover:text-[var(--text-primary)]">清除</Link>
              )}
            </div>
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-[var(--text-tertiary)]">類型</span>
              {(Object.entries(KIND_META) as [Kind, typeof KIND_META[Kind]][]).map(([k, m]) => {
                const count = kindCounts[k] || 0;
                if (!count) return null;
                const active = kindFilter === k;
                return (
                  <Link
                    key={k}
                    href={`/dq?days=${days}${sevFilter ? `&sev=${sevFilter}` : ""}${active ? "" : `&kind=${k}`}`}
                    className={cn(
                      "inline-flex items-center gap-1 px-2 py-0.5 rounded border",
                      active
                        ? "bg-[var(--brand-500)] text-white border-[var(--brand-500)]"
                        : "bg-surface text-[var(--text-tertiary)] border-[var(--border-default)] hover:border-[var(--brand-300)]",
                    )}
                  >
                    <Icon name={m.icon} size={12} />
                    {m.label} ({count})
                  </Link>
                );
              })}
              {kindFilter && (
                <Link href={`/dq?days=${days}${sevFilter ? `&sev=${sevFilter}` : ""}`} className="text-[var(--text-tertiary)] hover:text-[var(--text-primary)]">清除</Link>
              )}
            </div>
          </div>
        </section>
      )}

      {/* 異常列表 */}
      <section className="flex flex-col gap-3">
        <h2 className="text-base font-semibold inline-flex items-center gap-2">
          <Icon name="warning" size={20} className="text-[var(--brand-500)]" />
          價格異常
          <span className="numeric text-xs text-[var(--text-tertiary)] font-normal ml-2">
            {filtered.length} 筆{kindFilter || sevFilter ? `（已篩選自 ${dq.nAnomalies} 筆）` : ""}
          </span>
        </h2>
        {filtered.length === 0 ? (
          <EmptyState size="sm">
            {dq.nAnomalies === 0 ? "🎉 焦點股近期無異常" : "目前篩選下沒有結果"}
          </EmptyState>
        ) : (
          <TableContainer>
            <table className="w-full text-[15px] min-w-[800px]">
              <thead className="bg-subtle">
                <tr>
                  <Th align="left">嚴重度</Th>
                  <Th align="left">日期</Th>
                  <Th align="left">代號 / 名稱</Th>
                  <Th align="left">市場</Th>
                  <Th align="left">類型</Th>
                  <Th align="right">數值</Th>
                  <Th align="left">說明</Th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((a) => {
                  const sev = SEV_META[a.severity];
                  const km = KIND_META[a.kind];
                  return (
                    <tr key={`${a.stockId}-${a.date}-${a.kind}`} className="border-t border-[var(--border-default)] hover:bg-subtle">
                      <Td>
                        <span className={cn("inline-flex items-center px-2 py-0.5 rounded text-[11px] font-semibold border", sev.cls)}>
                          {sev.label}
                        </span>
                      </Td>
                      <Td numeric className="text-xs">{a.date}</Td>
                      <Td>
                        <StockIdCell stockId={a.stockId} stockName={a.stockName} />
                      </Td>
                      <Td>
                        {a.market && (
                          <span className={cn(
                            "text-[11px] px-1.5 py-0.5 rounded font-medium",
                            a.market === "ETF" ? "bg-[var(--info-bg)] text-[var(--info-fg)]" : "bg-subtle text-[var(--text-secondary)]",
                          )}>{a.market}</span>
                        )}
                      </Td>
                      <Td>
                        <span className="inline-flex items-center gap-1 text-xs">
                          <Icon name={km?.icon ?? "help"} size={14} className="text-[var(--text-tertiary)]" />
                          {km?.label ?? a.kind}
                        </span>
                      </Td>
                      <Td align="right" numeric className="text-xs">
                        {a.value != null ? (
                          a.kind === "volume_spike" || a.kind === "stale"
                            ? `${a.value.toFixed(1)}${a.kind === "volume_spike" ? "×" : " 日"}`
                            : fmtPct(a.value, 1)
                        ) : "—"}
                      </Td>
                      <Td className="text-xs text-[var(--text-secondary)]">{a.note}</Td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </TableContainer>
        )}
      </section>

      {/* 缺值列表 */}
      {dq.gaps.length > 0 && (
        <section className="flex flex-col gap-3">
          <h2 className="text-base font-semibold inline-flex items-center gap-2">
            <Icon name="link_off" size={20} className="text-[var(--warning-fg)]" />
            股票級資料缺值
            <span className="numeric text-xs text-[var(--text-tertiary)] font-normal ml-2">{dq.gaps.length} 筆 · 依缺日數降序</span>
          </h2>
          <TableContainer>
            <table className="w-full text-[15px] min-w-[700px]">
              <thead className="bg-subtle">
                <tr>
                  <Th>代號 / 名稱</Th>
                  <Th>表</Th>
                  <Th align="right">缺日數</Th>
                  <Th align="right">期望</Th>
                  <Th align="right">完整度</Th>
                </tr>
              </thead>
              <tbody>
                {dq.gaps.map((g) => {
                  const completeness = g.expected > 0 ? (g.expected - g.missingDays) / g.expected : 0;
                  return (
                    <tr key={`${g.stockId}-${g.table}`} className="border-t border-[var(--border-default)] hover:bg-subtle">
                      <Td>
                        <StockIdCell stockId={g.stockId} stockName={g.stockName} />
                      </Td>
                      <Td className="text-xs text-[var(--text-secondary)]">{g.table}</Td>
                      <Td align="right" numeric className="text-[var(--color-down)] font-semibold">{g.missingDays}</Td>
                      <Td align="right" numeric>{g.expected}</Td>
                      <Td align="right" numeric className={cn(completeness < 0.5 && "text-[var(--color-down)]")}>
                        {(completeness * 100).toFixed(0)}%
                      </Td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </TableContainer>
        </section>
      )}

      {/* 建議行動 */}
      {(dq.nAnomalies > 0 || dq.nGaps > 0) && (
        <section className="rounded-xl border border-[var(--info-border)] bg-[var(--info-bg)] p-4 text-sm text-[var(--info-fg)]">
          <div className="font-semibold inline-flex items-center gap-1.5 mb-1.5">
            <Icon name="tips_and_updates" size={16} filled />
            建議行動
          </div>
          <ul className="text-xs space-y-1 list-disc list-inside leading-relaxed">
            {sevCounts.critical > 0 && <li>「嚴重」異常請優先排查（漏抓除權息事件、極端跌幅）。可手動跑 <code className="font-mono">python -m scripts.update_adj</code> 補還原價</li>}
            {kindCounts.huge_gap > 0 && <li>跳空缺口股可能是漏抓除權息：跑 <code className="font-mono">python -m scripts.update_adj</code> 後重新評估</li>}
            {kindCounts.stale > 0 && <li>停滯股可能停牌；確認後可從自選股移除避免拖累評分</li>}
            {dq.nGaps > 0 && <li>缺值多的股票可重跑 <code className="font-mono">python -m scripts.market_update --days 60</code> 回補</li>}
          </ul>
        </section>
      )}
    </div>
  );
}

