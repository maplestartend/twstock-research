"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Icon } from "@/components/primitives/Icon";
import type { TradeRow } from "@/lib/api";

/**
 * Inline editor for trade journal: entry_reason / tags / note。
 * 預設 collapsed 顯示 chips；點 ✎ 切到編輯模式存後 router.refresh() 拿新資料。
 */
export function JournalEditor({ trade }: { trade: TradeRow }) {
  const router = useRouter();
  const [editing, setEditing] = useState(false);
  const [reason, setReason] = useState(trade.entryReason ?? "");
  const [tagsInput, setTagsInput] = useState(trade.tags ?? "");
  const [note, setNote] = useState(trade.note ?? "");
  const [busy, setBusy] = useState(false);

  const tagList = (trade.tags ?? "").split(",").map((s) => s.trim()).filter(Boolean);

  const save = async () => {
    setBusy(true);
    try {
      const res = await fetch(`/api/portfolio/trades/${trade.id}/journal`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          entryReason: reason,
          tags: tagsInput,
          note: note,
        }),
      });
      if (!res.ok) throw new Error(await res.text());
      setEditing(false);
      router.refresh();
    } catch (err) {
      console.error("save journal failed", err);
      alert("儲存失敗，請看 console");
    } finally {
      setBusy(false);
    }
  };

  if (!editing) {
    return (
      <div className="flex items-start gap-2">
        <div className="flex-1 flex flex-col gap-1 text-xs">
          {trade.entryReason ? (
            <span className="text-[var(--text-secondary)]">理由：{trade.entryReason}</span>
          ) : (
            <span className="text-[var(--text-tertiary)] italic">尚未填寫進場理由</span>
          )}
          <div className="flex flex-wrap gap-1">
            {tagList.length > 0 ? (
              tagList.map((tag) => (
                <span
                  key={tag}
                  className="inline-block px-1.5 py-0.5 rounded bg-[var(--brand-tint)] text-[var(--brand-700)] dark:text-[var(--brand-300)] text-[11px]"
                >
                  {tag}
                </span>
              ))
            ) : (
              <span className="text-[var(--text-tertiary)] italic">無標籤</span>
            )}
          </div>
          {trade.note && <span className="text-[var(--text-tertiary)]">備註：{trade.note}</span>}
        </div>
        <button
          type="button"
          onClick={() => setEditing(true)}
          title="編輯日誌"
          className="opacity-0 group-hover:opacity-100 transition-opacity inline-flex items-center justify-center w-8 h-8 rounded-md hover:bg-subtle"
        >
          <Icon name="edit" size={16} />
        </button>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2 text-xs">
      <input
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        placeholder="進場理由（例：法人連買 5 日 + MA20 上彎）"
        className="w-full h-8 px-2 rounded-md border border-[var(--border-default)] bg-surface text-xs"
        name={`reason-${trade.id}`}
        id={`reason-${trade.id}`}
      />
      <input
        value={tagsInput}
        onChange={(e) => setTagsInput(e.target.value)}
        placeholder="標籤（逗號分隔，例：短線強勢,法人連買）"
        className="w-full h-8 px-2 rounded-md border border-[var(--border-default)] bg-surface text-xs"
        name={`tags-${trade.id}`}
        id={`tags-${trade.id}`}
      />
      <input
        value={note}
        onChange={(e) => setNote(e.target.value)}
        placeholder="備註（不影響統計）"
        className="w-full h-8 px-2 rounded-md border border-[var(--border-default)] bg-surface text-xs"
        name={`note-${trade.id}`}
        id={`note-${trade.id}`}
      />
      <div className="flex gap-2">
        <button
          type="button"
          onClick={save}
          disabled={busy}
          className="h-7 px-3 rounded-md bg-[var(--brand-500)] text-white text-xs font-medium hover:bg-[var(--brand-600)] disabled:opacity-50"
        >
          {busy ? "儲存中…" : "儲存"}
        </button>
        <button
          type="button"
          onClick={() => {
            setEditing(false);
            setReason(trade.entryReason ?? "");
            setTagsInput(trade.tags ?? "");
            setNote(trade.note ?? "");
          }}
          className="h-7 px-3 rounded-md border border-[var(--border-default)] text-xs hover:bg-subtle"
        >
          取消
        </button>
      </div>
    </div>
  );
}
