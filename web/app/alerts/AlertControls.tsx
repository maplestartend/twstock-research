"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Icon } from "@/components/primitives/Icon";
import type { AlertRule } from "@/lib/api";

export function AlertControls({ rule }: { rule: AlertRule }) {
  const router = useRouter();
  const [busy, setBusy] = useState<"toggle" | "delete" | null>(null);

  const toggle = async () => {
    setBusy("toggle");
    try {
      await fetch(`/api/alerts/rules/${rule.id}/active?active=${!rule.active}`, { method: "PATCH" });
      router.refresh();
    } finally {
      setBusy(null);
    }
  };

  const remove = async () => {
    if (!confirm(`確定刪除 ${rule.stockId} 的「${rule.ruleKind}」規則？`)) return;
    setBusy("delete");
    try {
      await fetch(`/api/alerts/rules/${rule.id}`, { method: "DELETE" });
      router.refresh();
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="inline-flex items-center gap-2">
      <button
        type="button"
        onClick={toggle}
        disabled={busy !== null}
        title={rule.active ? "暫停" : "啟用"}
        className="inline-flex items-center justify-center w-8 h-8 rounded-md border border-[var(--border-default)] hover:bg-subtle disabled:opacity-50"
      >
        <Icon name={rule.active ? "pause" : "play_arrow"} size={16} />
      </button>
      <button
        type="button"
        onClick={remove}
        disabled={busy !== null}
        title="刪除"
        className="inline-flex items-center justify-center w-8 h-8 rounded-md border border-[var(--border-default)] hover:bg-[var(--color-down-bg)] hover:border-[var(--color-down-border)] hover:text-[var(--color-down)] disabled:opacity-50"
      >
        <Icon name="delete" size={16} />
      </button>
    </div>
  );
}
