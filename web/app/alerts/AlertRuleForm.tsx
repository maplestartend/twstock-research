"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Icon } from "@/components/primitives/Icon";
import type { AlertRuleKind } from "@/lib/api";

const KIND_OPTIONS: { value: AlertRuleKind; label: string; needsThreshold: boolean; thresholdLabel: string }[] = [
  { value: "price_below", label: "價格跌破", needsThreshold: true, thresholdLabel: "目標價（NT$）" },
  { value: "price_above", label: "價格突破", needsThreshold: true, thresholdLabel: "目標價（NT$）" },
  { value: "score_drop", label: "短期分數下跌", needsThreshold: true, thresholdLabel: "跌幅（分）" },
  { value: "score_rise", label: "短期分數上升", needsThreshold: true, thresholdLabel: "漲幅（分）" },
  { value: "atr_breached", label: "ATR 跌破（持股）", needsThreshold: false, thresholdLabel: "" },
];

export function AlertRuleForm() {
  const router = useRouter();
  const [stockId, setStockId] = useState("");
  const [kind, setKind] = useState<AlertRuleKind>("price_below");
  const [threshold, setThreshold] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const meta = KIND_OPTIONS.find((o) => o.value === kind)!;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const body: { stock_id: string; rule_kind: AlertRuleKind; threshold?: number; note?: string } = {
        stock_id: stockId.trim(),
        rule_kind: kind,
      };
      if (meta.needsThreshold) {
        const n = Number(threshold);
        if (!Number.isFinite(n) || n <= 0) {
          throw new Error("閾值必須是 > 0 的數字");
        }
        body.threshold = n;
      }
      if (note.trim()) body.note = note.trim();
      const res = await fetch("/api/alerts/rules", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(detail.detail ?? `${res.status} ${res.statusText}`);
      }
      setStockId("");
      setThreshold("");
      setNote("");
      router.refresh();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="rounded-xl border border-[var(--border-default)] bg-surface p-4 lg:p-5 flex flex-col gap-3">
      <div className="flex items-center gap-2">
        <Icon name="add_alert" size={20} className="text-[var(--brand-500)]" />
        <h2 className="text-base font-semibold">新增預警規則</h2>
      </div>
      <form onSubmit={submit} className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-xs text-[var(--text-secondary)]">
          代號
          <input
            value={stockId}
            onChange={(e) => setStockId(e.target.value)}
            required
            placeholder="2330"
            className="w-28 h-9 px-3 rounded-md border border-[var(--border-default)] bg-surface text-sm"
            name="stock_id"
            id="alert-stock-id"
          />
        </label>
        <label className="flex flex-col gap-1 text-xs text-[var(--text-secondary)]">
          類型
          <select
            value={kind}
            onChange={(e) => setKind(e.target.value as AlertRuleKind)}
            className="h-9 px-2 rounded-md border border-[var(--border-default)] bg-surface text-sm"
            name="rule_kind"
            id="alert-rule-kind"
          >
            {KIND_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
        </label>
        {meta.needsThreshold && (
          <label className="flex flex-col gap-1 text-xs text-[var(--text-secondary)]">
            {meta.thresholdLabel}
            <input
              type="number"
              step="0.01"
              min="0"
              value={threshold}
              onChange={(e) => setThreshold(e.target.value)}
              required
              className="w-28 h-9 px-3 rounded-md border border-[var(--border-default)] bg-surface text-sm numeric"
              name="threshold"
              id="alert-threshold"
            />
          </label>
        )}
        <label className="flex flex-col gap-1 text-xs text-[var(--text-secondary)] flex-1 min-w-[200px]">
          備註（選填）
          <input
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="例：跌破年線出場"
            className="h-9 px-3 rounded-md border border-[var(--border-default)] bg-surface text-sm"
            name="note"
            id="alert-note"
          />
        </label>
        <button
          type="submit"
          disabled={busy || !stockId.trim()}
          className="h-9 px-4 rounded-md bg-[var(--brand-500)] text-white text-sm font-medium hover:bg-[var(--brand-600)] disabled:opacity-50 inline-flex items-center gap-1.5"
        >
          <Icon name={busy ? "hourglass_empty" : "add"} size={16} />
          {busy ? "新增中…" : "新增"}
        </button>
      </form>
      {error && (
        <div className="text-xs text-[var(--color-down)] bg-[var(--color-down-bg)] border border-[var(--color-down-border)] rounded-md px-3 py-2">
          {error}
        </div>
      )}
    </section>
  );
}
