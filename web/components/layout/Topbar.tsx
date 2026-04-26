import { apiGetOptional, type MarketSnapshot, type MarketBreadth, type SnapshotStatus } from "@/lib/api";
import { fmtNum, fmtPrice, fmtPct, taipeiDate, taipeiWeekday, tone, toneIcon, toneLabel } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Icon } from "@/components/primitives/Icon";
import { ThemeToggle } from "@/components/primitives/ThemeToggle";
import { SearchTrigger } from "@/components/primitives/SearchTrigger";
import { SnapshotFreshnessIndicator } from "@/components/primitives/SnapshotFreshnessIndicator";
import { SidebarToggle } from "./SidebarToggle";

const TONE_CLASS = {
  up: "text-[var(--color-up)]",
  down: "text-[var(--color-down)]",
  flat: "text-[var(--color-flat)]",
};

export async function Topbar() {
  const [snap, breadth, snapshotStatus] = await Promise.all([
    apiGetOptional<MarketSnapshot>("/api/market/snapshot", { revalidate: 60 }),
    apiGetOptional<MarketBreadth>("/api/market/breadth", { revalidate: 60 }),
    apiGetOptional<SnapshotStatus>("/api/system/snapshot-status", { revalidate: 30 }),
  ]);
  const pct = snap?.changePct != null ? snap.changePct / 100 : null;
  const t = tone(pct);
  // 以台北時區計算，避免伺服器在 UTC 時區時日期顯示錯誤（午夜前後會差一天）
  const now = new Date();
  const today = taipeiDate(now);
  const todayWd = taipeiWeekday(now);
  // 若「今天」不等於加權指數最後一個交易日，額外顯示最近交易日
  const lastTrading = (snap?.date && snap.date !== today)
    ? { date: snap.date, weekday: taipeiWeekday(new Date(snap.date + "T00:00:00+08:00")) }
    : null;

  return (
    <header className="sticky top-0 z-20 h-16 shrink-0 flex items-center gap-3 lg:gap-6 px-4 lg:px-8 border-b border-[var(--border-default)] bg-surface/95 backdrop-blur supports-[backdrop-filter]:bg-surface/80">
      <SidebarToggle />
      {snap && (
        <div className="flex items-baseline gap-2 lg:gap-3">
          <span className="hidden sm:inline text-xs text-[var(--text-tertiary)] tracking-wide">加權指數</span>
          <span className="numeric text-base lg:text-xl font-bold">{fmtPrice(snap.close)}</span>
          {pct != null && (
            <span className={cn("numeric text-sm font-medium inline-flex items-center gap-0.5", TONE_CLASS[t])}>
              <Icon name={toneIcon(pct)} size={18} label={toneLabel(pct)} />
              {fmtPct(pct, 2)}
            </span>
          )}
        </div>
      )}
      {breadth && (
        <div className="hidden md:flex items-center gap-4 text-xs text-[var(--text-secondary)] border-l border-[var(--border-default)] pl-6">
          <span className="inline-flex items-center gap-3">
            <span className="inline-flex items-center gap-1 text-[var(--color-up)] font-medium">
              <Icon name="arrow_drop_up" size={16} />
              <span className="numeric">{fmtNum(breadth.nUp)}</span>
            </span>
            <span className="text-[var(--text-tertiary)]">/</span>
            <span className="inline-flex items-center gap-1 text-[var(--color-down)] font-medium">
              <Icon name="arrow_drop_down" size={16} />
              <span className="numeric">{fmtNum(breadth.nDown)}</span>
            </span>
          </span>
          {breadth.healthLabel && (
            <span className={cn(
              "px-2 py-0.5 rounded text-[11px] font-medium",
              breadth.healthTone === "up" && "bg-[var(--color-up-bg)] text-[var(--color-up)]",
              breadth.healthTone === "down" && "bg-[var(--color-down-bg)] text-[var(--color-down)]",
              breadth.healthTone === "neutral" && "bg-subtle text-[var(--text-secondary)]",
            )}>
              {breadth.healthLabel}
            </span>
          )}
        </div>
      )}
      <div className="ml-auto flex items-center gap-3 lg:gap-4">
        <SnapshotFreshnessIndicator initial={snapshotStatus} />
        <SearchTrigger />
        <div className="hidden sm:flex flex-col items-end leading-tight">
          <span className="numeric text-sm text-[var(--text-secondary)] inline-flex items-center gap-2">
            <Icon name="calendar_today" size={16} className="text-[var(--text-tertiary)]" />
            {today} 週{todayWd}
          </span>
          {lastTrading && (
            <span className="numeric text-[11px] text-[var(--text-tertiary)] mt-0.5">
              最近交易 {lastTrading.date} 週{lastTrading.weekday}
            </span>
          )}
        </div>
        <ThemeToggle />
      </div>
    </header>
  );
}
