"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Icon } from "@/components/primitives/Icon";

/** 「立即評估 (預覽)」按鈕：用 push=false 呼叫 check_alerts，回饋當下命中數 + 重抓規則表。
 *
 * push=false 不會推 Discord 也不會佔用 24h cooldown（fix in app/alerts.py），所以可放心連按。
 */
export function EvaluateButton() {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ count: number; ts: string } | null>(null);

  const onClick = async () => {
    setBusy(true);
    try {
      const res = await fetch("/api/alerts/check?push=false", { method: "POST" });
      if (!res.ok) {
        setResult({ count: -1, ts: new Date().toLocaleTimeString("zh-TW", { hour12: false, timeZone: "Asia/Taipei" }) });
        return;
      }
      const hits = (await res.json()) as Array<unknown>;
      setResult({ count: hits.length, ts: new Date().toLocaleTimeString("zh-TW", { hour12: false, timeZone: "Asia/Taipei" }) });
      router.refresh();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="inline-flex items-center gap-2 text-xs">
      <button
        type="button"
        onClick={onClick}
        disabled={busy}
        title="不推 Discord，只重新計算所有 active 規則的 actualValue / triggered"
        className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md border border-[var(--border-default)] bg-surface-1 hover:bg-subtle disabled:opacity-50"
      >
        <Icon name={busy ? "progress_activity" : "refresh"} size={14} className={busy ? "animate-spin" : ""} />
        立即評估 (預覽)
      </button>
      {result && (
        <span className="text-[var(--text-tertiary)]">
          {result.count < 0
            ? "評估失敗，請看 console"
            : result.count === 0
              ? `${result.ts}：無命中`
              : `${result.ts}：${result.count} 條命中`}
        </span>
      )}
    </div>
  );
}
