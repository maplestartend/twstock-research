"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect } from "react";
import { Icon } from "@/components/primitives/Icon";
import { cn } from "@/lib/utils";
import { useSidebar } from "./SidebarProvider";

// 側欄 8 項分 4 區塊：原本 14 項展平太擠，且回測類路由跟主流程混在一起。
// 解法：把 5 個回測/調優工具收進 /lab 中央 hub，自選股管理 / 除權息行事曆視為 /watchlist 的子頁。
// 路由保留不動（deep link 仍然 work），sidebar 只是視覺收斂。
type NavItem = { href: string; icon: string; label: string; matchPrefixes?: string[] };
type NavSection = { heading: string; items: NavItem[] };

const SECTIONS: NavSection[] = [
  {
    heading: "概覽",
    items: [
      { href: "/",         icon: "dashboard",   label: "今日戰情室" },
      { href: "/sectors",  icon: "trending_up", label: "族群輪動" },
    ],
  },
  {
    heading: "持股",
    items: [
      { href: "/holdings",  icon: "account_balance_wallet", label: "我的持股" },
      // 自選股 → 含 /watchlist-manage、/dividend-calendar 兩個子頁，靠 matchPrefixes 高亮父項
      { href: "/watchlist", icon: "dataset", label: "自選股", matchPrefixes: ["/watchlist", "/watchlist-manage", "/dividend-calendar"] },
      { href: "/journal",   icon: "edit_note", label: "交易日誌" },
    ],
  },
  {
    heading: "訊號",
    items: [
      { href: "/radar",   icon: "radar",   label: "雷達掃描" },
      { href: "/history", icon: "history", label: "歷史追蹤" },
      { href: "/alerts",  icon: "notifications_active", label: "預警" },
    ],
  },
  {
    heading: "進階分析",
    items: [
      // 回測工具室：/lab 是 hub，5 個子工具的 URL 都當作它的子頁
      { href: "/lab", icon: "science", label: "回測工具室",
        matchPrefixes: ["/lab", "/backtest", "/portfolio-backtest", "/grid-search", "/event-backtest", "/weight-tuner"] },
    ],
  },
  {
    heading: "系統",
    items: [
      { href: "/dq", icon: "health_and_safety", label: "資料品質" },
    ],
  },
];

function isActive(pathname: string, item: NavItem): boolean {
  const prefixes = item.matchPrefixes ?? [item.href];
  for (const p of prefixes) {
    if (p === "/") {
      if (pathname === "/") return true;
      continue;
    }
    if (pathname === p || pathname.startsWith(p + "/")) return true;
  }
  return false;
}

export function Sidebar() {
  const pathname = usePathname() || "/";
  const { open, setOpen } = useSidebar();

  // 換頁時自動關閉 mobile drawer（route change 後 pathname 變動）
  useEffect(() => {
    if (open) setOpen(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pathname]);

  return (
    <>
      {/* Mobile backdrop — 點擊關閉 */}
      <div
        className={cn(
          "fixed inset-0 z-30 bg-black/50 backdrop-blur-sm transition-opacity lg:hidden",
          open ? "opacity-100" : "opacity-0 pointer-events-none",
        )}
        onClick={() => setOpen(false)}
        aria-hidden="true"
      />
      <aside
        className={cn(
          // 共用：全高、邊框、背景
          "h-screen w-60 shrink-0 border-r border-[var(--border-default)] bg-surface flex flex-col",
          // Desktop (lg+)：內嵌 sticky，drawer 行為關閉
          "lg:sticky lg:top-0 lg:translate-x-0 lg:z-10",
          // Mobile：fixed 滑入式 drawer
          "fixed inset-y-0 left-0 z-40 transition-transform duration-200 ease-out",
          open ? "translate-x-0" : "-translate-x-full lg:translate-x-0",
        )}
        aria-label="主選單"
      >
        <div className="h-16 shrink-0 flex items-center gap-3 px-5 border-b border-[var(--border-default)]">
          <Icon name="candlestick_chart" size={22} className="text-[var(--brand-500)]" filled />
          <span className="font-bold text-[15px] text-[var(--text-primary)]">台股研究儀表板</span>
          <button
            type="button"
            name="sidebar-close"
            onClick={() => setOpen(false)}
            className="ml-auto lg:hidden p-1.5 rounded-md hover:bg-subtle text-[var(--text-secondary)]"
            aria-label="關閉選單"
          >
            <Icon name="close" size={20} />
          </button>
        </div>
        <nav className="flex-1 py-3 overflow-y-auto">
          <div className="flex flex-col gap-3 px-2">
            {SECTIONS.map((section) => (
              <div key={section.heading} className="flex flex-col gap-0.5">
                <div className="px-3 pt-1 pb-1 text-[11px] font-semibold uppercase tracking-wider text-[var(--text-tertiary)]">
                  {section.heading}
                </div>
                <ul className="flex flex-col gap-0.5">
                  {section.items.map((item) => {
                    const active = isActive(pathname, item);
                    return (
                      <li key={item.label}>
                        <Link
                          href={item.href}
                          aria-current={active ? "page" : undefined}
                          className={cn(
                            "relative flex items-center gap-3 h-10 px-3 text-sm rounded-md transition-colors",
                            active
                              ? "bg-[var(--info-bg)] text-[var(--info-fg)] font-semibold before:content-[''] before:absolute before:left-0 before:top-1.5 before:bottom-1.5 before:w-0.5 before:rounded-r before:bg-[var(--brand-500)]"
                              : "font-medium text-[var(--text-secondary)] hover:bg-subtle hover:text-[var(--text-primary)]"
                          )}
                        >
                          <Icon name={item.icon} size={20} filled={active} />
                          <span>{item.label}</span>
                        </Link>
                      </li>
                    );
                  })}
                </ul>
              </div>
            ))}
          </div>
        </nav>
        <div className="shrink-0 px-4 py-3 border-t border-[var(--border-default)] text-xs text-[var(--text-tertiary)]">
          <div>Design System v1</div>
          <div className="mt-0.5">紅漲綠跌 · 台股慣例</div>
        </div>
      </aside>
    </>
  );
}
