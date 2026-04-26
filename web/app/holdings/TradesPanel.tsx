"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { Icon } from "@/components/primitives/Icon";
import { EmptyState } from "@/components/primitives/EmptyState";
import { Th, Td } from "@/components/primitives/Table";
import { apiDelete, apiPost, type TradeRow } from "@/lib/api";
import { btnDestructive, btnPrimary, inputCls } from "@/lib/formClasses";
import { fmtMoney, fmtPrice } from "@/lib/format";
import { cn } from "@/lib/utils";

type Notice = { kind: "ok" | "err"; msg: string };

function todayISO(): string {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

export function TradesPanel({ initialTrades }: { initialTrades: TradeRow[] }) {
  const router = useRouter();
  const [isPending, startTransition] = useTransition();

  const [tradeDate, setTradeDate] = useState(todayISO());
  const [stockId, setStockId] = useState("");
  const [action, setAction] = useState<"BUY" | "SELL">("BUY");
  const [shares, setShares] = useState("");          // 張數（1 張 = 1000 股）
  const [price, setPrice] = useState("");
  const [note, setNote] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [deletingId, setDeletingId] = useState<number | null>(null);

  const flash = (kind: Notice["kind"], msg: string) => {
    setNotice({ kind, msg });
    setTimeout(() => setNotice(null), 3500);
  };

  const refresh = () => startTransition(() => router.refresh());

  const resetForm = () => {
    setStockId(""); setShares(""); setPrice(""); setNote("");
    setAction("BUY"); setTradeDate(todayISO());
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const sid = stockId.trim();
    const sharesNum = Number(shares) * 1000;   // 張 → 股
    const priceNum = Number(price);
    if (!sid) return flash("err", "請填代號");
    if (!sharesNum || sharesNum <= 0) return flash("err", "張數需 > 0");
    if (!priceNum || priceNum <= 0) return flash("err", "成交價需 > 0");

    setSubmitting(true);
    try {
      await apiPost<TradeRow>("/api/portfolio/trades", {
        trade_date: tradeDate,
        stock_id: sid,
        action,
        shares: sharesNum,
        price: priceNum,
        note: note.trim() || null,
      });
      flash("ok", `已新增 ${action === "BUY" ? "買入" : "賣出"} ${sid} ${shares} 張 @ ${priceNum}`);
      resetForm();
      refresh();
    } catch (err) {
      flash("err", `新增失敗：${(err as Error).message}`);
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async (t: TradeRow) => {
    const ok = window.confirm(
      `確定刪除這筆交易？\n\n${t.tradeDate} ${t.action === "BUY" ? "買入" : "賣出"} ${t.stockId} ${(t.shares / 1000).toFixed(1)} 張 @ ${t.price}\n\n刪除後會用 trade_log 重建該股 holdings。`,
    );
    if (!ok) return;
    setDeletingId(t.id);
    try {
      await apiDelete(`/api/portfolio/trades/${t.id}`);
      flash("ok", `已刪除 ${t.stockId} ${t.tradeDate} 的交易`);
      refresh();
    } catch (err) {
      flash("err", `刪除失敗：${(err as Error).message}`);
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <section className="flex flex-col gap-4">
      <div className="flex items-center gap-2">
        <Icon name="receipt_long" size={20} className="text-[var(--brand-500)]" />
        <h2 className="text-base font-semibold text-[var(--text-primary)]">交易紀錄</h2>
      </div>

      {/* 新增交易表單 */}
      <form
        onSubmit={handleSubmit}
        className="rounded-xl border border-[var(--border-default)] bg-surface p-4 flex flex-col gap-3"
      >
        <div className="flex items-center gap-2 text-sm font-semibold text-[var(--text-secondary)]">
          <Icon name="add_circle" size={18} className="text-[var(--brand-500)]" />
          新增交易
        </div>
        <div className="grid grid-cols-2 md:grid-cols-6 gap-2">
          <label className="flex flex-col gap-1 text-xs text-[var(--text-tertiary)]">
            交易日
            <input
              type="date"
              value={tradeDate}
              onChange={(e) => setTradeDate(e.target.value)}
              className={inputCls}
              required
            />
          </label>
          <label className="flex flex-col gap-1 text-xs text-[var(--text-tertiary)]">
            代號
            <input
              type="text"
              value={stockId}
              onChange={(e) => setStockId(e.target.value)}
              placeholder="2330"
              className={cn(inputCls, "numeric")}
              required
            />
          </label>
          <label className="flex flex-col gap-1 text-xs text-[var(--text-tertiary)]">
            買 / 賣
            <select
              value={action}
              onChange={(e) => setAction(e.target.value as "BUY" | "SELL")}
              className={inputCls}
            >
              <option value="BUY">買入</option>
              <option value="SELL">賣出</option>
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs text-[var(--text-tertiary)]">
            張數
            <input
              type="number"
              value={shares}
              onChange={(e) => setShares(e.target.value)}
              placeholder="1"
              step="0.001"
              min="0"
              className={cn(inputCls, "numeric")}
              required
            />
          </label>
          <label className="flex flex-col gap-1 text-xs text-[var(--text-tertiary)]">
            成交價
            <input
              type="number"
              value={price}
              onChange={(e) => setPrice(e.target.value)}
              placeholder="600"
              step="0.01"
              min="0"
              className={cn(inputCls, "numeric")}
              required
            />
          </label>
          <label className="flex flex-col gap-1 text-xs text-[var(--text-tertiary)]">
            備註（選填）
            <input
              type="text"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder=""
              className={inputCls}
            />
          </label>
        </div>
        <div className="flex items-center gap-3">
          <button type="submit" className={btnPrimary} disabled={submitting || isPending}>
            <Icon name={submitting ? "hourglass_empty" : "save"} size={16} />
            {submitting ? "送出中…" : "新增"}
          </button>
          <span className="text-xs text-[var(--text-tertiary)]">
            手續費（0.1425%）與證交稅（賣方 0.3%）由後端依券商規則自動計算。
          </span>
        </div>
        {notice && (
          <div
            className={cn(
              "inline-flex items-center gap-2 text-sm px-3 py-2 rounded border self-start",
              notice.kind === "ok"
                ? "bg-[var(--color-down-bg)] text-[var(--color-down)] border-[var(--color-down-border)]"
                : "bg-[var(--error-bg)] text-[var(--error-fg)] border-[var(--error-border)]",
            )}
          >
            <Icon name={notice.kind === "ok" ? "check_circle" : "error"} size={16} filled />
            {notice.msg}
          </div>
        )}
      </form>

      {/* 交易紀錄表格 */}
      {initialTrades.length === 0 ? (
        <EmptyState size="sm">尚無交易紀錄</EmptyState>
      ) : (
        <div className="rounded-xl border border-[var(--border-default)] bg-surface overflow-x-auto">
          <table className="w-full text-sm min-w-[900px] table-fixed">
            <thead className="bg-subtle">
              <tr className="text-[11px] uppercase tracking-wide text-[var(--text-secondary)]">
                <Th className="w-[110px]">交易日</Th>
                <Th className="w-[140px]">代號 / 名稱</Th>
                <Th align="right" className="w-[80px]">動作</Th>
                <Th align="right" className="w-[100px]">股數</Th>
                <Th align="right" className="w-[90px]">成交價</Th>
                <Th align="right" className="w-[110px]">金額</Th>
                <Th align="right" className="w-[120px]">手續費 / 稅</Th>
                <Th>備註</Th>
                <Th align="right" className="w-[80px]">操作</Th>
              </tr>
            </thead>
            <tbody>
              {initialTrades.map((t) => {
                const isBuy = t.action === "BUY";
                return (
                  <tr key={t.id} className="border-t border-[var(--border-default)] hover:bg-subtle transition-colors">
                    <Td numeric>{t.tradeDate}</Td>
                    <Td>
                      <Link href={`/stocks/${t.stockId}`} className="flex flex-col hover:underline">
                        <span className="numeric font-semibold text-[var(--text-primary)]">{t.stockId}</span>
                        <span className="text-[var(--text-tertiary)] text-xs truncate">{t.stockName ?? ""}</span>
                      </Link>
                    </Td>
                    <Td align="right">
                      <span className={cn(
                        "inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium",
                        isBuy
                          ? "bg-[var(--color-up-bg)] text-[var(--color-up)]"
                          : "bg-[var(--color-down-bg)] text-[var(--color-down)]",
                      )}>
                        <Icon name={isBuy ? "add_shopping_cart" : "sell"} size={12} />
                        {isBuy ? "買入" : "賣出"}
                      </span>
                    </Td>
                    <Td align="right" numeric>{t.shares.toLocaleString("zh-TW")}</Td>
                    <Td align="right" numeric>{fmtPrice(t.price)}</Td>
                    <Td align="right" numeric>{fmtMoney(t.shares * t.price, 0)}</Td>
                    <Td align="right" numeric>
                      <span className="text-xs text-[var(--text-tertiary)]">
                        {fmtMoney(t.fee ?? 0, 0)}
                        {(t.tax ?? 0) > 0 && ` / ${fmtMoney(t.tax!, 0)}`}
                      </span>
                    </Td>
                    <Td>
                      <span className="text-xs text-[var(--text-tertiary)] truncate inline-block max-w-full">{t.note ?? "—"}</span>
                    </Td>
                    <Td align="right">
                      <button
                        type="button"
                        onClick={() => handleDelete(t)}
                        disabled={deletingId === t.id || isPending}
                        className={cn(btnDestructive, "h-7 px-2 text-xs")}
                        title="刪除此筆並重建該股 holdings"
                      >
                        <Icon name="delete" size={14} />
                        {deletingId === t.id ? "…" : "刪除"}
                      </button>
                    </Td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <p className="text-xs text-[var(--text-tertiary)]">
        最多顯示 50 筆。刪除一筆後會自動以 trade_log 重建該股 holdings 的 shares / avg_cost。
      </p>
    </section>
  );
}
