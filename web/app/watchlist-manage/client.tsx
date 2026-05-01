"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState, useTransition } from "react";
import { Icon } from "@/components/primitives/Icon";
import { EmptyState } from "@/components/primitives/EmptyState";
import { Field } from "@/components/primitives/Field";
import { Th, TdCompact as Td } from "@/components/primitives/Table";
import { apiGet, apiPost, apiPut } from "@/lib/api";
import { btnDestructive, btnPrimary, btnSecondary, inputCls } from "@/lib/formClasses";
import { cn } from "@/lib/utils";

type Entry = { stockId: string; stockName: string; tags: string[] };

export function WatchlistManageClient({ initialEntries }: { initialEntries: Entry[] }) {
  const router = useRouter();
  const [isPending, startTransition] = useTransition();
  const [entries, setEntries] = useState<Entry[]>(initialEntries);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [notice, setNotice] = useState<{ kind: "ok" | "warn" | "err"; msg: string } | null>(null);

  // 新增表單
  const [addSid, setAddSid] = useState("");
  const [previewName, setPreviewName] = useState("");   // 代號對應的資料庫名稱（唯讀預覽）
  const [lookedUp, setLookedUp] = useState("");          // 上次 lookup 的 sid

  // 批次
  const [bulkText, setBulkText] = useState("");
  const [bulkOpen, setBulkOpen] = useState(false);

  // 代號輸入 debounce lookup
  useEffect(() => {
    const sid = addSid.trim();
    if (!sid) {
      setPreviewName("");
      setLookedUp("");
      return;
    }
    if (sid === lookedUp) return;
    const timer = setTimeout(async () => {
      try {
        const r = await apiGet<{ stockName: string | null }>(`/api/watchlist/lookup/${encodeURIComponent(sid)}`, { noCache: true });
        setLookedUp(sid);
        setPreviewName(r.stockName ?? "");
      } catch { /* ignore */ }
    }, 400);
    return () => clearTimeout(timer);
  }, [addSid, lookedUp]);

  const refresh = () => {
    startTransition(() => {
      router.refresh();
    });
  };

  const flash = (kind: "ok" | "warn" | "err", msg: string) => {
    setNotice({ kind, msg });
    setTimeout(() => setNotice(null), 3000);
  };

  const exists = (sid: string) => entries.some((e) => e.stockId === sid);

  const handleAdd = async () => {
    const sid = addSid.trim();
    if (!sid) return;
    if (exists(sid)) {
      flash("warn", `${sid} 已在自選清單`);
      return;
    }
    try {
      const r = await apiPost<{ ok: boolean; stockName: string }>("/api/watchlist", { stock_id: sid });
      const name = r.stockName || sid;
      setEntries((arr) => [...arr, { stockId: sid, stockName: name, tags: [] }]
        .sort((a, b) => a.stockId.localeCompare(b.stockId)));
      setAddSid(""); setPreviewName(""); setLookedUp("");
      flash("ok", `已新增 ${sid} ${name}`);
      refresh();
    } catch (e) {
      flash("err", `新增失敗：${(e as Error).message}`);
    }
  };

  /** 覆寫單檔的所有 tags：local state 先樂觀更新，後端失敗時還原並 flash error。 */
  const updateTags = async (sid: string, tags: string[]) => {
    const prev = entries;
    setEntries((arr) => arr.map((e) => (e.stockId === sid ? { ...e, tags } : e)));
    try {
      await apiPut<{ tags: string[] }>(`/api/watchlist/${encodeURIComponent(sid)}/tags`, { tags });
    } catch (e) {
      setEntries(prev);  // rollback
      flash("err", `更新標籤失敗：${(e as Error).message}`);
    }
  };

  const handleBulkAdd = async () => {
    const ids = bulkText.split(/[\s,，]+/).map((x) => x.trim()).filter(Boolean);
    if (ids.length === 0) return;
    try {
      const r = await apiPost<{ added: number; skipped: number }>("/api/watchlist/bulk-add", { stock_ids: ids });
      flash("ok", `批次新增 ${r.added} 檔（略過 ${r.skipped}）`);
      setBulkText("");
      setBulkOpen(false);
      // 拉一次最新列表
      const list = await apiGet<Entry[]>("/api/watchlist", { noCache: true });
      setEntries(list);
      refresh();
    } catch (e) {
      flash("err", `批次新增失敗：${(e as Error).message}`);
    }
  };

  const toggleSelect = (sid: string) => {
    setSelected((s) => {
      const next = new Set(s);
      if (next.has(sid)) next.delete(sid); else next.add(sid);
      return next;
    });
  };

  const handleBulkRemove = async () => {
    if (selected.size === 0) return;
    if (!confirm(`確定刪除 ${selected.size} 檔？`)) return;
    const ids = Array.from(selected);
    try {
      const r = await apiPost<{ removed: number }>("/api/watchlist/bulk-remove", { stock_ids: ids });
      setEntries((arr) => arr.filter((e) => !selected.has(e.stockId)));
      setSelected(new Set());
      flash("ok", `已刪除 ${r.removed} 檔`);
      refresh();
    } catch (e) {
      flash("err", `刪除失敗：${(e as Error).message}`);
    }
  };

  return (
    <>
      {/* Notice toast */}
      {notice && (
        <div className={cn(
          "rounded-lg border px-4 py-2 text-sm inline-flex items-center gap-2",
          notice.kind === "ok" && "bg-[var(--color-down-bg)] text-[var(--color-down)] border-[var(--color-down-border)]",
          notice.kind === "warn" && "bg-[var(--warning-bg)] text-[var(--warning-fg)] border-[var(--warning-border)]",
          notice.kind === "err" && "bg-[var(--error-bg)] text-[var(--error-fg)] border-[var(--error-border)]",
        )}>
          <Icon name={notice.kind === "err" ? "error" : notice.kind === "warn" ? "warning" : "check_circle"} size={16} filled />
          {notice.msg}
        </div>
      )}

      {/* ➕ 新增 */}
      <section className="flex flex-col gap-3">
        <h2 className="text-base font-semibold inline-flex items-center gap-2">
          <Icon name="add_circle" size={20} className="text-[var(--brand-500)]" />
          新增
        </h2>
        <div className="rounded-xl border border-[var(--border-default)] bg-surface p-4 flex flex-col gap-3 md:flex-row md:items-end">
          <Field label="代號" className="md:w-32">
            <input
              type="text"
              value={addSid}
              onChange={(e) => setAddSid(e.target.value)}
              placeholder="2330"
              className={cn(inputCls, "w-full")}
              onKeyDown={(e) => e.key === "Enter" && handleAdd()}
            />
          </Field>
          <Field label="名稱（依代號自動帶出）" className="flex-1">
            <input
              type="text"
              value={previewName}
              placeholder="輸入代號會自動帶出"
              className={cn(inputCls, "w-full bg-subtle cursor-not-allowed")}
              readOnly
              disabled
            />
          </Field>
          <button
            onClick={handleAdd}
            disabled={!addSid.trim() || isPending}
            className={btnPrimary}
          >
            <Icon name="add" size={16} />
            新增
          </button>
        </div>
        <button
          onClick={() => setBulkOpen((v) => !v)}
          className="inline-flex items-center gap-1 self-start text-sm text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
        >
          <Icon name={bulkOpen ? "expand_less" : "expand_more"} size={18} />
          批次貼上多檔代號
        </button>
        {bulkOpen && (
          <div className="rounded-xl border border-[var(--border-default)] bg-surface p-4 flex flex-col gap-3">
            <textarea
              value={bulkText}
              onChange={(e) => setBulkText(e.target.value)}
              placeholder="一行一個代號（可用空白、逗號分隔）&#10;2330&#10;2317&#10;2454"
              rows={5}
              className={cn(inputCls, "w-full font-mono resize-y")}
            />
            <button onClick={handleBulkAdd} disabled={!bulkText.trim() || isPending} className={cn(btnSecondary, "self-start")}>
              <Icon name="playlist_add" size={16} />
              批次新增
            </button>
          </div>
        )}
      </section>

      {/* 📋 目前清單 */}
      <section className="flex flex-col gap-3">
        <div className="flex items-center justify-between">
          <h2 className="text-base font-semibold inline-flex items-center gap-2">
            <Icon name="format_list_bulleted" size={20} className="text-[var(--brand-500)]" />
            目前清單 <span className="numeric text-xs text-[var(--text-tertiary)] font-normal ml-1">{entries.length} 檔</span>
          </h2>
          {selected.size > 0 && (
            <button onClick={handleBulkRemove} disabled={isPending} className={btnDestructive}>
              <Icon name="delete" size={16} />
              刪除勾選的 {selected.size} 檔
            </button>
          )}
        </div>

        {entries.length === 0 ? (
          <EmptyState>還沒有自選股，用上方新增。</EmptyState>
        ) : (
          <div className="rounded-xl border border-[var(--border-default)] bg-surface overflow-hidden">
            <table className="w-full text-[15px]">
              <thead className="bg-subtle">
                <tr>
                  <Th className="w-10" align="center">
                    <input
                      type="checkbox"
                      checked={entries.length > 0 && selected.size === entries.length}
                      onChange={() => setSelected(
                        selected.size === entries.length ? new Set() : new Set(entries.map((e) => e.stockId))
                      )}
                      aria-label="全選"
                    />
                  </Th>
                  <Th className="w-28">代號</Th>
                  <Th className="w-40">名稱</Th>
                  <Th>標籤</Th>
                  <Th className="w-24" align="right" />
                </tr>
              </thead>
              <tbody>
                {entries.map((e) => (
                  <tr key={e.stockId} className={cn(
                    "border-t border-[var(--border-default)] transition-colors",
                    selected.has(e.stockId) ? "bg-[var(--brand-tint)]" : "hover:bg-subtle",
                  )}>
                    <Td align="center">
                      <input
                        type="checkbox"
                        checked={selected.has(e.stockId)}
                        onChange={() => toggleSelect(e.stockId)}
                        aria-label={`選擇 ${e.stockId}`}
                      />
                    </Td>
                    <Td>
                      <span className="numeric font-semibold text-[var(--text-primary)]">{e.stockId}</span>
                    </Td>
                    <Td>
                      <span className="text-[var(--text-primary)]">{e.stockName}</span>
                    </Td>
                    <Td>
                      <TagsEditor
                        tags={e.tags}
                        onChange={(next) => updateTags(e.stockId, next)}
                      />
                    </Td>
                    <Td align="right">
                      <a href={`/stocks/${e.stockId}`} className="inline-flex items-center gap-1 text-xs text-[var(--text-tertiary)] hover:text-[var(--brand-600)]">
                        詳情
                        <Icon name="chevron_right" size={14} />
                      </a>
                    </Td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}

/**
 * 單列 tag 編輯器：chip 上點 × 移除、輸入框 Enter 新增。
 * 樂觀更新由 caller 處理（updateTags），這裡只負責「local 編輯狀態」。
 */
function TagsEditor({
  tags,
  onChange,
}: {
  tags: string[];
  onChange: (next: string[]) => void;
}) {
  const [draft, setDraft] = useState("");

  const submit = () => {
    const t = draft.trim();
    if (!t) return;
    if (tags.includes(t)) {
      setDraft("");
      return;  // 已存在，視為 noop（後端也會 dedup）
    }
    onChange([...tags, t]);
    setDraft("");
  };

  const remove = (tag: string) => onChange(tags.filter((t) => t !== tag));

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {tags.map((t) => (
        <span
          key={t}
          className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-xs bg-[var(--brand-tint)] text-[var(--brand-700)] border border-[var(--brand-300)]/40"
        >
          {t}
          <button
            type="button"
            onClick={() => remove(t)}
            className="hover:text-[var(--text-primary)] -mr-0.5"
            aria-label={`移除標籤 ${t}`}
          >
            <Icon name="close" size={12} />
          </button>
        </span>
      ))}
      <input
        type="text"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            submit();
          }
        }}
        onBlur={submit}
        placeholder={tags.length === 0 ? "+ 新增標籤" : "+"}
        className="text-xs px-2 py-0.5 rounded-md border border-dashed border-[var(--border-default)] bg-transparent w-24 focus:w-32 focus:border-[var(--brand-500)] focus:outline-none transition-[width,border-color]"
      />
    </div>
  );
}
