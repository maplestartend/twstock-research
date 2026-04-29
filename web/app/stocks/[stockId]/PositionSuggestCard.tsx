"use client";

import { useEffect, useMemo, useState } from "react";
import { Icon } from "@/components/primitives/Icon";
import { apiPost, type PositionSuggestRequest, type PositionSuggestResponse } from "@/lib/api";
import { cn } from "@/lib/utils";

/** 「現價買 → 停損 X → 建議 N 張 → 最大虧 Y / 資金 Z%」決策卡。
 *
 * 把已有的 ATR 固定式停損 + /api/portfolio/position-suggest 整合：使用者只要在這頁
 * 看到分數 → 看到停損 → 直接拿到「該買幾張、會不會超過風險預算」，不必跳到「新增交易」表單試算。
 *
 * 設計：capital/risk 偏好用 localStorage 持久化（per-browser），entry/stop 由父層 props 帶下來。
 * stop_price 必須 < entry_price（後端 422），所以這裡也預先 guard。
 */
export function PositionSuggestCard({
  stockId,
  entryPrice,
  stopPrice,
}: {
  stockId: string;
  entryPrice: number | null;
  stopPrice: number | null;
}) {
  const [capital, setCapital] = useState<number>(1_000_000);
  const [riskPct, setRiskPct] = useState<number>(2.0); // %, 0.5–10
  const [result, setResult] = useState<PositionSuggestResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  // 載入持久化偏好
  useEffect(() => {
    try {
      const cap = Number(localStorage.getItem("posSuggest.capital"));
      if (Number.isFinite(cap) && cap > 0) setCapital(cap);
      const rp = Number(localStorage.getItem("posSuggest.riskPct"));
      if (Number.isFinite(rp) && rp > 0 && rp <= 10) setRiskPct(rp);
    } catch {
      /* localStorage 不可用就用預設值 */
    }
  }, []);
  useEffect(() => {
    try { localStorage.setItem("posSuggest.capital", String(capital)); } catch {}
  }, [capital]);
  useEffect(() => {
    try { localStorage.setItem("posSuggest.riskPct", String(riskPct)); } catch {}
  }, [riskPct]);

  const canCompute =
    entryPrice != null && stopPrice != null &&
    entryPrice > 0 && stopPrice > 0 && stopPrice < entryPrice &&
    capital > 0 && riskPct > 0 && riskPct <= 50;

  // 自動試算（debounce 200ms）
  useEffect(() => {
    if (!canCompute) {
      setResult(null);
      setError(stopPrice != null && entryPrice != null && stopPrice >= entryPrice
        ? "停損價必須 < 進場價"
        : null);
      return;
    }
    const handle = setTimeout(async () => {
      setLoading(true);
      setError(null);
      try {
        const body: PositionSuggestRequest = {
          capital,
          entry_price: entryPrice!,
          stop_price: stopPrice!,
          risk_per_trade: riskPct / 100,
          lot_size: 1000,
        };
        const res = await apiPost<PositionSuggestResponse>("/api/portfolio/position-suggest", body);
        setResult(res);
      } catch (e) {
        setError((e as Error).message);
        setResult(null);
      } finally {
        setLoading(false);
      }
    }, 200);
    return () => clearTimeout(handle);
  }, [capital, riskPct, entryPrice, stopPrice, canCompute]);

  const positionPct = useMemo(() => {
    if (!result || capital <= 0) return null;
    return result.maxPositionValue / capital;
  }, [result, capital]);

  // 高價股 + 小本金 → 連 1 張都買不起；提示使用者調整本金或考慮零股。
  // 1 張市值 = entry × 1000；風險限額足夠買 1 張 (risk_per_share × 1000 ≤ risk_amount)
  // 但本金不夠時 risk.py 會 floor 成 0 張。
  const lotCost = entryPrice != null ? entryPrice * 1000 : null;
  const lotAffordable = lotCost != null && lotCost <= capital;
  const showOneLotWarning = result != null && result.maxShares === 0 && lotCost != null && !lotAffordable;

  return (
    <div className="rounded-xl border border-[var(--border-default)] bg-surface p-4 lg:p-5 flex flex-col gap-4">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-2">
          <Icon name="receipt_long" size={18} className="text-[var(--brand-500)]" />
          <span className="text-sm font-semibold text-[var(--text-primary)]">買進試算</span>
          <span className="text-xs text-[var(--text-tertiary)]">固定比例風險法 · 自動套用 ATR 固定停損</span>
        </div>
        <div className="flex items-center gap-3 text-xs">
          <label className="flex items-center gap-1.5 text-[var(--text-secondary)]">
            本金
            <input
              type="number"
              min={10000}
              step={10000}
              value={capital}
              onChange={(e) => setCapital(Number(e.target.value) || 0)}
              className="w-28 h-7 px-2 rounded border border-[var(--border-default)] bg-surface numeric text-right"
            />
          </label>
          <label className="flex items-center gap-1.5 text-[var(--text-secondary)]">
            單筆風險
            <input
              type="number"
              min={0.1}
              max={10}
              step={0.1}
              value={riskPct}
              onChange={(e) => setRiskPct(Number(e.target.value) || 0)}
              className="w-16 h-7 px-2 rounded border border-[var(--border-default)] bg-surface numeric text-right"
            />
            %
          </label>
        </div>
      </div>

      {!canCompute ? (
        <div className="text-xs text-[var(--text-tertiary)] py-3">
          {entryPrice == null || stopPrice == null
            ? "缺少現價或 ATR 固定停損 → 無法試算（資料不足或需要先抓盤後 OHLCV）"
            : error ?? "輸入有誤"}
        </div>
      ) : (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <Stat
            label="建議張數"
            value={result ? `${result.maxLots.toFixed(2)} 張` : "—"}
            sub={result ? `${result.maxShares.toLocaleString("zh-TW")} 股 · 1 張=1,000 股` : ""}
            tone="brand"
            loading={loading && !result}
          />
          <Stat
            label="部位市值"
            value={result ? formatNTD(result.maxPositionValue) : "—"}
            sub={positionPct != null ? `佔本金 ${(positionPct * 100).toFixed(1)}%` : ""}
            loading={loading && !result}
          />
          <Stat
            label="最大虧損"
            value={result ? formatNTD(result.riskAmount) : "—"}
            sub={result ? `每股 ${formatNTD(result.riskPerShare)} × ${result.maxShares.toLocaleString("zh-TW")} 股` : ""}
            tone="down"
            loading={loading && !result}
          />
          <Stat
            label="現價 → 停損"
            value={entryPrice != null && stopPrice != null
              ? `${entryPrice.toFixed(2)} → ${stopPrice.toFixed(2)}`
              : "—"}
            sub={entryPrice != null && stopPrice != null
              ? `跌幅 ${(((entryPrice - stopPrice) / entryPrice) * 100).toFixed(2)}%`
              : ""}
          />
        </div>
      )}

      {error && canCompute && (
        <div className="text-xs text-[var(--color-down)] bg-[var(--color-down-bg)] border border-[var(--color-down-border)] rounded-md px-3 py-2">
          {error}
        </div>
      )}

      {showOneLotWarning && (
        <div className="text-xs text-[var(--warning-fg)] bg-[var(--warning-bg)] border border-[var(--warning-border)] rounded-md px-3 py-2 inline-flex items-start gap-2">
          <Icon name="warning" size={14} className="mt-0.5" />
          <span>
            1 張要 {formatNTD(lotCost!)}，超過目前本金 {formatNTD(capital)} →
            建議調高本金（這頁的設定只影響試算，不影響實際下單），或在新增交易時改用「零股」(lot_size=1)。
          </span>
        </div>
      )}

      <p className="text-[11px] text-[var(--text-tertiary)] leading-relaxed">
        本金 / 風險 % 偏好會記在這台瀏覽器（localStorage）。
        進場價 = {stockId} 最近收盤；停損 = ATR 固定式停損（2×ATR）。實際下單請另外考慮手續費與滑價（系統其他頁的回測已含）。
      </p>
    </div>
  );
}

type StatTone = "default" | "brand" | "down";

function Stat({
  label,
  value,
  sub,
  tone = "default",
  loading = false,
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: StatTone;
  loading?: boolean;
}) {
  const valueCls =
    tone === "brand" ? "text-[var(--brand-500)]" :
    tone === "down" ? "text-[var(--color-down)]" :
    "text-[var(--text-primary)]";
  return (
    <div className="flex flex-col gap-1">
      <span className="text-[11px] text-[var(--text-tertiary)] uppercase tracking-wide">{label}</span>
      <span className={cn("numeric text-[22px] font-bold leading-none", valueCls, loading && "opacity-50")}>
        {value}
      </span>
      {sub && <span className="text-[11px] text-[var(--text-tertiary)]">{sub}</span>}
    </div>
  );
}

function formatNTD(v: number): string {
  return v.toLocaleString("zh-TW", { maximumFractionDigits: 0, style: "currency", currency: "TWD" });
}
