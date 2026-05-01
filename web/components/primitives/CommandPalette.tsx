"use client";

import { useEffect, useRef, useState, useCallback, useMemo } from "react";
import { useRouter } from "next/navigation";
import { Icon } from "@/components/primitives/Icon";
import { cn } from "@/lib/utils";

type SearchHit = {
  stockId: string;
  stockName: string;
  market: string | null;
  industry: string | null;
  inWatchlist: boolean;
};

type Action = {
  id: string;
  label: string;
  hint?: string;
  icon: string;
  href: string;
  keywords: string;
};

// 跨頁面快捷導航 — 鍵入時也會匹配
const ACTIONS: Action[] = [
  { id: "go-home", label: "今日戰情室", icon: "dashboard", href: "/", keywords: "home dashboard 戰情" },
  { id: "go-lab", label: "回測工具室", icon: "science", href: "/lab", keywords: "lab 回測工具室 research" },
  { id: "go-diagnostics", label: "因子檢定", icon: "insights", href: "/diagnostics", keywords: "diagnostics 因子檢定 ic" },
  { id: "go-radar", label: "雷達掃描", icon: "radar", href: "/radar", keywords: "radar 雷達 個股" },
  { id: "go-radar-etf", label: "雷達 — ETF", icon: "currency_exchange", href: "/radar?type=etf", keywords: "etf radar" },
  { id: "go-sectors", label: "族群輪動", icon: "trending_up", href: "/sectors", keywords: "sector industry 族群" },
  { id: "go-watchlist", label: "自選股總覽", icon: "dataset", href: "/watchlist", keywords: "watchlist 自選" },
  { id: "go-holdings", label: "我的持股", icon: "account_balance_wallet", href: "/holdings", keywords: "holdings 持股" },
  { id: "go-dividend", label: "除權息行事曆", icon: "event", href: "/dividend-calendar", keywords: "dividend 除權息" },
  { id: "go-history", label: "歷史追蹤", icon: "history", href: "/history", keywords: "history 歷史" },
  { id: "go-watchlist-mng", label: "自選股管理", icon: "edit_note", href: "/watchlist-manage", keywords: "watchlist manage 管理" },
  { id: "go-backtest", label: "策略回測", icon: "replay", href: "/backtest", keywords: "backtest 回測" },
  { id: "go-portfolio-bt", label: "投組回測", icon: "bar_chart", href: "/portfolio-backtest", keywords: "portfolio backtest 投組" },
  { id: "go-grid", label: "參數掃描", icon: "science", href: "/grid-search", keywords: "grid search 參數 walk forward" },
  { id: "go-event-bt", label: "除權息回測", icon: "redeem", href: "/event-backtest", keywords: "event backtest dividend 除權息 套息" },
  { id: "go-weight", label: "權重調優", icon: "tune", href: "/weight-tuner", keywords: "weight 權重" },
  { id: "go-dq", label: "資料品質", icon: "fact_check", href: "/dq", keywords: "data quality dq 資料品質 異常" },
];
const DEFAULT_ACTION_IDS = new Set([
  "go-home",
  "go-radar",
  "go-lab",
  "go-diagnostics",
  "go-holdings",
  "go-watchlist",
]);

const BASE = process.env.NEXT_PUBLIC_API_BASE ?? "";

export function CommandPalette() {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [activeIdx, setActiveIdx] = useState(0);
  const [loading, setLoading] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const router = useRouter();

  // 全域 hotkey: ⌘/Ctrl + K + 自訂 event 觸發（讓 SearchTrigger 按鈕可開啟）
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const isModK = (e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k";
      if (isModK) {
        e.preventDefault();
        setOpen((o) => !o);
      } else if (e.key === "Escape" && open) {
        setOpen(false);
      }
    };
    const opener = () => setOpen(true);
    window.addEventListener("keydown", handler);
    window.addEventListener("commandpalette:open", opener);
    return () => {
      window.removeEventListener("keydown", handler);
      window.removeEventListener("commandpalette:open", opener);
    };
  }, [open]);

  // 開啟時自動 focus + 鎖背景：對 landmark (header / aside / main / nav / footer) 套 inert + aria-hidden，
  // 鎖 body scroll；關閉後復原焦點到原本觸發開啟的元素。
  // 鎖 landmarks 而非 body > *，是因為 dialog 自身渲染在 layout 內、會被 body > * 一併鎖到。
  useEffect(() => {
    if (!open) return;
    setQ("");
    setActiveIdx(0);
    const previouslyFocused = document.activeElement as HTMLElement | null;
    requestAnimationFrame(() => inputRef.current?.focus());

    const inertTargets: HTMLElement[] = Array.from(
      document.querySelectorAll<HTMLElement>("body header, body aside, body main, body nav, body footer"),
    );
    inertTargets.forEach((el) => {
      el.setAttribute("inert", "");
      el.setAttribute("aria-hidden", "true");
    });
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    return () => {
      inertTargets.forEach((el) => {
        el.removeAttribute("inert");
        el.removeAttribute("aria-hidden");
      });
      document.body.style.overflow = prevOverflow;
      previouslyFocused?.focus?.();
    };
  }, [open]);

  // 搜尋（debounced + AbortController：快打字時 in-flight 請求要被取消）
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    const ctl = new AbortController();
    setLoading(true);
    const t = setTimeout(async () => {
      try {
        const url = `${BASE}/api/search/stocks?q=${encodeURIComponent(q)}&limit=12`;
        const res = await fetch(url, { signal: ctl.signal });
        if (!res.ok) throw new Error("search failed");
        const data = (await res.json()) as SearchHit[];
        if (!cancelled) {
          setHits(data);
          setActiveIdx(0);
        }
      } catch (err) {
        // AbortError 不算錯（單純被新請求取代），其他錯誤才清空
        if (!cancelled && (err as Error).name !== "AbortError") setHits([]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }, 120);
    return () => {
      cancelled = true;
      ctl.abort();
      clearTimeout(t);
    };
  }, [q, open]);

  // 過濾頁面 actions。空查詢時顯示常用捷徑，降低新手探索成本。
  const matchedActions = useMemo<Action[]>(() => {
    const query = q.trim().toLowerCase();
    if (!query) return ACTIONS.filter((a) => DEFAULT_ACTION_IDS.has(a.id));
    return ACTIONS.filter((a) =>
      a.label.toLowerCase().includes(query) || a.keywords.toLowerCase().includes(query),
    );
  }, [q]);

  // 合併結果列表 — 順序：actions → stocks
  const items = useMemo(() => {
    const list: Array<
      | { kind: "action"; data: Action }
      | { kind: "stock"; data: SearchHit }
    > = [];
    matchedActions.forEach((a) => list.push({ kind: "action", data: a }));
    hits.forEach((h) => list.push({ kind: "stock", data: h }));
    return list;
  }, [matchedActions, hits]);

  const navigate = useCallback(
    (href: string) => {
      router.push(href);
      setOpen(false);
    },
    [router],
  );

  const selectActive = useCallback(() => {
    const it = items[activeIdx];
    if (!it) return;
    if (it.kind === "action") navigate(it.data.href);
    else navigate(`/stocks/${it.data.stockId}`);
  }, [items, activeIdx, navigate]);

  const onKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIdx((i) => Math.min(i + 1, Math.max(0, items.length - 1)));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIdx((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      selectActive();
    } else if (e.key === "Tab") {
      // 簡易 focus trap：modal 內只有 input 一個 tabbable 元素，
      // Tab 直接 prevent 防止 focus 跑出 modal 到底層頁面
      e.preventDefault();
    }
  };

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center pt-[10vh] px-4 bg-black/40 backdrop-blur-sm"
      onClick={() => setOpen(false)}
      role="presentation"
    >
      <div
        className="w-full max-w-xl rounded-xl border border-[var(--border-default)] bg-surface shadow-2xl overflow-hidden flex flex-col"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="全域搜尋"
      >
        <div className="flex items-center gap-2 px-4 h-12 border-b border-[var(--border-default)]">
          <Icon name="search" size={18} className="text-[var(--text-tertiary)]" />
          <input
            ref={inputRef}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="輸入代號 / 名稱 / 頁面... (Esc 關閉)"
            className="flex-1 bg-transparent outline-none text-sm text-[var(--text-primary)] placeholder:text-[var(--text-tertiary)]"
          />
          {loading && <Icon name="progress_activity" size={16} className="text-[var(--text-tertiary)] animate-spin" />}
          <kbd className="text-[11px] px-1.5 py-0.5 rounded border border-[var(--border-default)] text-[var(--text-tertiary)] font-mono">
            Esc
          </kbd>
        </div>

        <ul className="max-h-[60vh] overflow-y-auto py-1">
          {items.length === 0 && !loading && (
            <li className="px-4 py-6 text-center text-sm text-[var(--text-tertiary)]">
              {q ? `查不到符合「${q}」的結果` : "輸入關鍵字開始搜尋..."}
            </li>
          )}
          {q.trim() === "" && matchedActions.length > 0 && (
            <li className="px-4 pt-2 pb-1 text-[11px] uppercase tracking-wide text-[var(--text-tertiary)]">
              常用頁面捷徑
            </li>
          )}
          {q.trim() === "" && hits.length > 0 && (
            <li className="px-4 pt-2 pb-1 text-[11px] uppercase tracking-wide text-[var(--text-tertiary)]">
              自選股快速跳轉
            </li>
          )}
          {items.map((it, idx) => {
            const active = idx === activeIdx;
            if (it.kind === "action") {
              return (
                <li key={it.data.id}>
                  <button
                    type="button"
                    onMouseEnter={() => setActiveIdx(idx)}
                    onClick={() => navigate(it.data.href)}
                    className={cn(
                      "w-full text-left flex items-center gap-3 px-4 h-10 text-sm transition-colors",
                      active ? "bg-subtle" : "hover:bg-subtle",
                    )}
                  >
                    <Icon name={it.data.icon} size={18} className="text-[var(--brand-500)]" />
                    <span className="flex-1 text-[var(--text-primary)]">{it.data.label}</span>
                    <span className="text-[11px] text-[var(--text-tertiary)]">頁面</span>
                  </button>
                </li>
              );
            }
            const h = it.data;
            return (
              <li key={h.stockId}>
                <button
                  type="button"
                  onMouseEnter={() => setActiveIdx(idx)}
                  onClick={() => navigate(`/stocks/${h.stockId}`)}
                  className={cn(
                    "w-full text-left flex items-center gap-3 px-4 h-12 text-sm transition-colors",
                    active ? "bg-subtle" : "hover:bg-subtle",
                  )}
                >
                  <span className="numeric font-semibold text-[var(--text-primary)] w-16 shrink-0">{h.stockId}</span>
                  <span className="flex-1 truncate text-[var(--text-secondary)]">{h.stockName}</span>
                  {h.industry && (
                    <span className="text-[11px] text-[var(--text-tertiary)] truncate max-w-[8rem]">{h.industry}</span>
                  )}
                  {h.market && (
                    <span className={cn(
                      "text-[11px] px-1.5 py-0.5 rounded font-medium shrink-0",
                      h.market === "ETF" ? "bg-[var(--info-bg)] text-[var(--info-fg)]" : "bg-subtle text-[var(--text-secondary)]",
                    )}>
                      {h.market}
                    </span>
                  )}
                  {h.inWatchlist && (
                    <Icon name="star" size={14} className="text-[var(--brand-500)] shrink-0" filled />
                  )}
                </button>
              </li>
            );
          })}
        </ul>

        <div className="px-4 h-9 border-t border-[var(--border-default)] flex items-center gap-3 text-[11px] text-[var(--text-tertiary)] bg-subtle">
          <span><kbd className="font-mono">↑↓</kbd> 移動</span>
          <span><kbd className="font-mono">Enter</kbd> 選擇</span>
          <span><kbd className="font-mono">Esc</kbd> 關閉</span>
          <span className="ml-auto">⌘ / Ctrl + K 隨時開啟</span>
        </div>
      </div>
    </div>
  );
}
