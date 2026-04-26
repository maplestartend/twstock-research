import Link from "next/link";
import {
  apiGet,
  apiGetOptional,
  type IndustryRotationRow,
  type IndustryRotationResponse,
  type IndustryMemberRow,
  type MarketBreadth,
} from "@/lib/api";
import { Icon } from "@/components/primitives/Icon";
import { PageHeader } from "@/components/primitives/PageHeader";
import { EmptyState } from "@/components/primitives/EmptyState";
import { PriceCell } from "@/components/primitives/PriceCell";
import { BackendDownError } from "@/components/primitives/BackendDownError";
import { Th, TdCompact as Td } from "@/components/primitives/Table";
import { SectionTitle } from "@/components/primitives/SectionTitle";
import { IndustryHeatmap } from "@/components/charts/IndustryHeatmap";
import { fmtNum, fmtPct, tone } from "@/lib/format";
import { cn } from "@/lib/utils";

export const revalidate = 60;

const PCT_COLS: { key: keyof IndustryRotationRow; label: string }[] = [
  { key: "ret1D",  label: "1日" },
  { key: "ret5D",  label: "5日" },
  { key: "ret20D", label: "20日" },
  { key: "ret60D", label: "60日" },
];

export default async function SectorsPage({
  searchParams,
}: {
  searchParams: Promise<{ industry?: string }>;
}) {
  const sp = await searchParams;
  const { industry } = sp;

  if (industry) {
    let members: IndustryMemberRow[] | null;
    try {
      members = await apiGetOptional<IndustryMemberRow[]>(
        `/api/market/industry-members?industry=${encodeURIComponent(industry)}&top=30`,
      );
    } catch (e) {
      return <BackendDownError error={e} pageTitle="族群輪動" />;
    }

    return (
      <div className="p-4 lg:p-8 flex flex-col gap-6 max-w-[1600px] mx-auto">
        <Breadcrumb
          items={[
            { label: "族群輪動", href: "/sectors" },
            { label: industry },
          ]}
        />
        <PageHeader
          title={industry}
          icon="groups"
          description="此產業成員股近 20 日表現（依綜合熱度排序，最多 30 檔）。"
        />
        {!members || members.length === 0 ? (
          <EmptyState size="sm">無成員資料</EmptyState>
        ) : (
          <div className="rounded-xl border border-[var(--border-default)] bg-surface overflow-x-auto">
            <table className="w-full text-sm min-w-[600px]">
              <thead className="bg-subtle">
                <tr className="text-[11px] uppercase tracking-wide text-[var(--text-secondary)]">
                  <Th>代號 / 名稱</Th>
                  <Th align="right">收盤</Th>
                  <Th align="right">1日</Th>
                  <Th align="right">5日</Th>
                  <Th align="right">20日</Th>
                </tr>
              </thead>
              <tbody>
                {members.map((m) => (
                  <tr key={m.stockId} className="border-t border-[var(--border-default)] hover:bg-subtle transition-colors">
                    <Td>
                      <Link href={`/stocks/${m.stockId}`} className="flex flex-col hover:underline">
                        <span className="numeric font-semibold text-[var(--text-primary)]">{m.stockId}</span>
                        <span className="text-[var(--text-tertiary)] text-xs">{m.stockName}</span>
                      </Link>
                    </Td>
                    <Td align="right">
                      <PriceCell price={m.close} deltaPct={m.ret1D} variant="compact" />
                    </Td>
                    <Td align="right" numeric>
                      <span className={toneClass(m.ret1D)}>{fmtPct(m.ret1D, 2)}</span>
                    </Td>
                    <Td align="right" numeric>
                      <span className={toneClass(m.ret5D)}>{fmtPct(m.ret5D, 2)}</span>
                    </Td>
                    <Td align="right" numeric>
                      <span className={toneClass(m.ret20D)}>{fmtPct(m.ret20D, 2)}</span>
                    </Td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    );
  }

  let breadth: MarketBreadth | null, rotationResp: IndustryRotationResponse;
  try {
    [breadth, rotationResp] = await Promise.all([
      apiGetOptional<MarketBreadth>("/api/market/breadth"),
      apiGet<IndustryRotationResponse>("/api/market/industry-rotation"),
    ]);
  } catch (e) {
    return <BackendDownError error={e} pageTitle="族群輪動" />;
  }
  const rotation: IndustryRotationRow[] = rotationResp.rows;
  const asOf = rotationResp.asOf;

  return (
    <div className="p-4 lg:p-8 flex flex-col gap-8 max-w-[1600px] mx-auto">
      <PageHeader
        title="族群輪動"
        icon="trending_up"
        description="各產業多時間窗平均報酬（等權）。點表格任一產業可看成員股。"
      />

      {/* Breadth strip */}
      {breadth && (
        <section className="grid grid-cols-2 lg:grid-cols-4 gap-4">
          <BreadthCard
            label="漲 / 跌"
            primary={`${fmtNum(breadth.nUp)} / ${fmtNum(breadth.nDown)}`}
            sub={breadth.advanceDeclineRatio != null ? `AD = ${breadth.advanceDeclineRatio.toFixed(2)}` : undefined}
          />
          <BreadthCard
            label="站上 MA20"
            primary={breadth.pctAboveMa20 != null ? `${(breadth.pctAboveMa20 * 100).toFixed(0)}%` : "—"}
          />
          <BreadthCard
            label="站上 MA60"
            primary={breadth.pctAboveMa60 != null ? `${(breadth.pctAboveMa60 * 100).toFixed(0)}%` : "—"}
          />
          <BreadthCard
            label="近 50 日 新高 / 新低"
            primary={`${fmtNum(breadth.nNewHigh50D)} / ${fmtNum(breadth.nNewLow50D)}`}
            sub={breadth.newHighLowRatio != null ? `比 ${breadth.newHighLowRatio.toFixed(2)}` : undefined}
          />
        </section>
      )}

      {/* 產業熱力圖（hero）— treemap：磚塊面積=成交值占比 / 顏色=當日成交值加權報酬（紅漲綠跌）。
          UIUX 審查 P0：treemap 是這頁的 headline insight，先放上面、表格往下擺。 */}
      {rotation.length > 0 && (
        <section className="flex flex-col gap-3">
          <SectionTitle icon="grid_view">產業熱力圖</SectionTitle>
          <div className="min-h-[480px]">
            <IndustryHeatmap data={rotation} asOf={asOf} />
          </div>
        </section>
      )}

      {/* Industry rotation table（細節） */}
      <section className="flex flex-col gap-3">
        <SectionTitle icon="bar_chart">產業熱度排行</SectionTitle>
        {rotation.length === 0 ? (
          <EmptyState size="sm">
            無資料。請先跑 <code className="font-mono">python -m scripts.market_update</code> 與{" "}
            <code className="font-mono">python -m scripts.refresh_industry</code>。
          </EmptyState>
        ) : (
          <div className="rounded-xl border border-[var(--border-default)] bg-surface overflow-x-auto">
            <table className="w-full text-sm min-w-[700px]">
              <thead className="bg-subtle">
                <tr className="text-[11px] uppercase tracking-wide text-[var(--text-secondary)]">
                  <Th>產業</Th>
                  <Th align="right">成員數</Th>
                  {PCT_COLS.map((c) => (
                    <Th key={c.key} align="right">{c.label}</Th>
                  ))}
                  <Th align="right">熱度</Th>
                  <Th align="right" />
                </tr>
              </thead>
              <tbody>
                {rotation.map((r) => (
                  <tr
                    key={r.industry}
                    className="border-t border-[var(--border-default)] hover:bg-subtle transition-colors"
                  >
                    <Td>
                      <Link
                        href={`/sectors?industry=${encodeURIComponent(r.industry)}`}
                        className="font-semibold text-[var(--text-primary)] hover:text-[var(--brand-600)] hover:underline"
                      >
                        {r.industry}
                      </Link>
                    </Td>
                    <Td align="right" numeric>{r.nMembers}</Td>
                    {PCT_COLS.map((c) => {
                      const v = r[c.key] as number | null;
                      return (
                        <Td key={c.key} align="right" numeric>
                          <span className={toneClass(v)}>{fmtPct(v, 2)}</span>
                        </Td>
                      );
                    })}
                    <Td align="right" numeric>
                      <span className={cn("font-semibold", toneClass(r.heat))}>
                        {fmtPct(r.heat, 2)}
                      </span>
                    </Td>
                    <Td align="right">
                      <Link
                        href={`/sectors?industry=${encodeURIComponent(r.industry)}`}
                        className="inline-flex items-center gap-1 text-xs font-medium px-2 py-1 rounded text-[var(--brand-600)] hover:bg-subtle"
                      >
                        看成員
                        <Icon name="chevron_right" size={14} />
                      </Link>
                    </Td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

function toneClass(v: number | null | undefined): string {
  const t = tone(v);
  if (t === "up") return "text-[var(--color-up)] font-medium";
  if (t === "down") return "text-[var(--color-down)] font-medium";
  return "text-[var(--text-secondary)]";
}

function BreadthCard({ label, primary, sub }: { label: string; primary: string; sub?: string }) {
  return (
    <div className="rounded-xl border border-[var(--border-default)] bg-surface p-4 flex flex-col gap-1">
      <span className="text-xs text-[var(--text-tertiary)]">{label}</span>
      <span className="numeric text-xl font-bold text-[var(--text-primary)]">{primary}</span>
      {sub && <span className="numeric text-xs text-[var(--text-tertiary)]">{sub}</span>}
    </div>
  );
}


type Crumb = { label: string; href?: string };

function Breadcrumb({ items }: { items: Crumb[] }) {
  return (
    <nav aria-label="breadcrumb" className="text-sm">
      <ol className="flex items-center flex-wrap gap-x-1 gap-y-1 text-[var(--text-tertiary)]">
        {items.map((it, i) => {
          const last = i === items.length - 1;
          return (
            <li key={i} className="inline-flex items-center gap-1">
              {it.href && !last ? (
                <Link
                  href={it.href}
                  className="inline-flex items-center gap-1 hover:text-[var(--text-primary)] hover:underline"
                >
                  {i === 0 && <Icon name="arrow_back" size={14} />}
                  {it.label}
                </Link>
              ) : (
                <span
                  aria-current={last ? "page" : undefined}
                  className={cn(last && "text-[var(--text-primary)] font-medium")}
                >
                  {it.label}
                </span>
              )}
              {!last && <Icon name="chevron_right" size={14} className="text-[var(--text-disabled)]" />}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
