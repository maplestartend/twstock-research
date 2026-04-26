import Link from "next/link";
import { apiGet, type ExDividendCalendarEvent } from "@/lib/api";
import { Icon } from "@/components/primitives/Icon";
import { PageHeader } from "@/components/primitives/PageHeader";
import { EmptyState } from "@/components/primitives/EmptyState";
import { BackendDownError } from "@/components/primitives/BackendDownError";
import { Th, Td } from "@/components/primitives/Table";
import { DownloadCsvButton } from "@/components/primitives/DownloadCsvButton";
import { fmtPrice, fmtPct, fmtDate } from "@/lib/format";
import { cn } from "@/lib/utils";

export const revalidate = 1800;  // 30 min，對齊後端 cache TTL

const DAYS_OPTIONS: readonly number[] = [7, 30, 60, 90];
const TABS = [
  { key: "holdings", label: "持股", icon: "account_balance_wallet" },
  { key: "watchlist", label: "自選", icon: "star" },
  { key: "all", label: "全部", icon: "public" },
] as const;

type Tab = typeof TABS[number]["key"];

export default async function DividendCalendarPage({
  searchParams,
}: {
  searchParams: Promise<{ days?: string; tab?: string }>;
}) {
  const sp = await searchParams;
  const days = DAYS_OPTIONS.includes(Number(sp.days)) ? Number(sp.days) : 60;
  const tab = (TABS.find((t) => t.key === sp.tab)?.key ?? "all") as Tab;

  let all: ExDividendCalendarEvent[];
  try {
    all = await apiGet<ExDividendCalendarEvent[]>(`/api/calendar/ex-dividend?days_ahead=${days}`);
  } catch (e) {
    return <BackendDownError error={e} pageTitle="除權息行事曆" />;
  }
  const filtered = tab === "holdings"
    ? all.filter((e) => e.inHoldings)
    : tab === "watchlist"
    ? all.filter((e) => e.inWatchlist)
    : all;

  const counts = {
    holdings: all.filter((e) => e.inHoldings).length,
    watchlist: all.filter((e) => e.inWatchlist).length,
    all: all.length,
  };

  return (
    <div className="p-4 lg:p-8 flex flex-col gap-8 max-w-[1400px] mx-auto">
      <PageHeader
        title="除權息行事曆"
        icon="event"
        description="未來 N 天內要除權息的股票，含殖利率估算（資料來自證交所 TWT49U，後端快取 30 分鐘）"
      />

      {/* Filters */}
      <section className="flex flex-wrap items-center gap-4 text-sm">
        <div className="flex items-center gap-2">
          <span className="text-[var(--text-tertiary)]">未來</span>
          {DAYS_OPTIONS.map((n) => {
            const active = n === days;
            return (
              <Link
                key={n}
                href={`/dividend-calendar?${new URLSearchParams({ days: String(n), tab }).toString()}`}
                scroll={false}
                className={cn(
                  "numeric px-2.5 py-1 rounded border text-xs transition-colors",
                  active
                    ? "bg-[var(--brand-500)] text-white border-[var(--brand-500)]"
                    : "bg-surface text-[var(--text-tertiary)] border-[var(--border-default)] hover:border-[var(--brand-300)]",
                )}
              >
                {n} 天
              </Link>
            );
          })}
        </div>
      </section>

      {/* Tabs */}
      <section className="flex items-center justify-between gap-3 flex-wrap">
        <div className="inline-flex items-center gap-1 p-1 rounded-lg bg-subtle border border-[var(--border-default)]">
          {TABS.map((t) => {
            const active = tab === t.key;
            return (
              <Link
                key={t.key}
                href={`/dividend-calendar?${new URLSearchParams({ days: String(days), tab: t.key }).toString()}`}
                scroll={false}
                className={cn(
                  "inline-flex items-center gap-2 px-3 py-1.5 rounded-md text-sm transition-colors",
                  active
                    ? "bg-surface shadow-sm text-[var(--text-primary)] font-medium"
                    : "text-[var(--text-tertiary)] hover:text-[var(--text-secondary)]",
                )}
              >
                <Icon name={t.icon} size={16} filled={active} />
                {t.label}
                <span className="numeric text-xs opacity-70">{counts[t.key]}</span>
              </Link>
            );
          })}
        </div>
        <DownloadCsvButton
          headers={DIV_CSV_HEADERS}
          rows={filtered.map((e) => [
            e.exDate, e.stockId, e.stockName, e.eventType ?? "",
            e.cumPrice ?? "", e.exPrice ?? "", e.dividendValue ?? "", e.yieldPct ?? "",
            e.inHoldings, e.inWatchlist,
          ])}
          filename={`ex_dividend_${tab}_${days}d`}
          size="sm"
        />
      </section>

      {/* Table */}
      <section className="flex flex-col gap-3">
        {filtered.length === 0 ? (
          <EmptyState>
            {tab === "holdings"
              ? "持股近期無除權息事件"
              : tab === "watchlist"
              ? "自選股近期無除權息事件"
              : "近期無除權息事件"}
          </EmptyState>
        ) : (
          <div className="rounded-xl border border-[var(--border-default)] bg-surface overflow-x-auto">
            <table className="w-full text-sm min-w-[700px]">
              <thead className="bg-subtle">
                <tr className="text-[11px] uppercase tracking-wide text-[var(--text-secondary)]">
                  <Th>除權息日</Th>
                  <Th>代號 / 名稱</Th>
                  <Th align="right">類型</Th>
                  <Th align="right">前收盤</Th>
                  <Th align="right">參考價</Th>
                  <Th align="right">權息值</Th>
                  <Th align="right">殖利率</Th>
                  <Th align="right" />
                </tr>
              </thead>
              <tbody>
                {filtered.map((e, i) => (
                  <tr key={`${e.stockId}-${e.exDate}-${i}`} className="border-t border-[var(--border-default)] hover:bg-subtle transition-colors">
                    <Td numeric>{fmtDate(e.exDate)}</Td>
                    <Td>
                      <Link href={`/stocks/${e.stockId}`} className="flex items-center gap-2 hover:underline">
                        <div className="flex flex-col">
                          <span className="numeric font-semibold text-[var(--text-primary)]">{e.stockId}</span>
                          <span className="text-[var(--text-tertiary)] text-xs">{e.stockName}</span>
                        </div>
                        <div className="flex gap-0.5 ml-1">
                          {e.inHoldings && (
                            <Icon name="account_balance_wallet" size={12} filled className="text-[var(--brand-500)]" label="持股" />
                          )}
                          {e.inWatchlist && (
                            <Icon name="star" size={12} filled className="text-[var(--warning-500)]" label="自選" />
                          )}
                        </div>
                      </Link>
                    </Td>
                    <Td align="right">
                      {e.eventType && (
                        // 「權」「息」對投資人都是正面事件，不該套漲跌色（避免誤以為「息」=下跌）
                        // 用中性 info 色區分類型
                        <span className={cn(
                          "inline-flex items-center px-1.5 py-0.5 rounded text-[11px] font-medium",
                          "bg-[var(--info-bg)] text-[var(--info-fg)] border border-[var(--info-border)]",
                        )}>
                          {e.eventType}
                        </span>
                      )}
                    </Td>
                    <Td align="right" numeric>{fmtPrice(e.cumPrice)}</Td>
                    <Td align="right" numeric>{fmtPrice(e.exPrice)}</Td>
                    <Td align="right" numeric>{fmtPrice(e.dividendValue)}</Td>
                    <Td align="right" numeric>
                      <span className={cn(
                        "font-medium",
                        e.yieldPct != null && e.yieldPct >= 0.05 && "text-[var(--color-up)]",
                      )}>
                        {fmtPct(e.yieldPct, 2)}
                      </span>
                    </Td>
                    <Td align="right">
                      <Link href={`/stocks/${e.stockId}`} className="inline-flex items-center gap-0.5 text-xs text-[var(--text-tertiary)] hover:text-[var(--brand-600)]">
                        詳情
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

const DIV_CSV_HEADERS = [
  "除權息日", "代號", "名稱", "類型",
  "前收盤", "參考價", "權息值", "殖利率",
  "持股", "自選",
];
