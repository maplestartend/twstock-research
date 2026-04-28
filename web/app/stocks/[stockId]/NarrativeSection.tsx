"use client";

/**
 * AI 解讀（個股綜合敘事）區塊。
 *
 * 設計要點：
 * - On-demand：使用者按按鈕才打 LLM，避免進頁面就無故扣 credit。
 * - 永久快取：後端用 (stock_id, as_of) 當 PK，同一日重複按只會讀 DB。
 * - 後端沒設 ANTHROPIC_API_KEY → /api/system/narrative-status 回 available=false
 *   → 整區塊隱藏（按鈕灰掉會誤導使用者以為是 bug）。
 * - 第一次按需要等 LLM 回應 (~3-5s)；快取命中 < 100ms。
 */
import { useEffect, useState } from "react";
import { Icon } from "@/components/primitives/Icon";
import { SectionTitle } from "@/components/primitives/SectionTitle";
import {
  apiGetOptional,
  apiPost,
  type NarrativeStatus,
  type NarrativeView,
} from "@/lib/api";

export function NarrativeSection({ stockId }: { stockId: string }) {
  const [status, setStatus] = useState<NarrativeStatus | null>(null);
  const [data, setData] = useState<NarrativeView | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 開頁先打 status 探測：未啟用就直接不渲染整個區塊
  useEffect(() => {
    apiGetOptional<NarrativeStatus>(`/api/system/narrative-status`).then(setStatus);
  }, []);

  if (!status) return null;             // 還在打 status → 不渲染避免閃爍
  if (!status.available) return null;   // 沒設 API key → 整區塊隱藏

  async function generate(force = false) {
    setLoading(true);
    setError(null);
    try {
      const path = `/api/stocks/${stockId}/narrative${force ? "?refresh=1" : ""}`;
      const result = await apiPost<NarrativeView>(path);
      setData(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="flex flex-col gap-3">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <SectionTitle icon="psychology">AI 解讀（散戶導向）</SectionTitle>
        <span className="text-[11px] text-[var(--text-tertiary)] font-mono">{status.model}</span>
      </div>

      <div className="rounded-xl border border-[var(--border-default)] bg-surface p-5 flex flex-col gap-3">
        {!data && !loading && !error && (
          <div className="flex flex-col gap-3">
            <p className="text-sm text-[var(--text-secondary)] leading-relaxed">
              用 LLM 把目前的三維分數 + 籌碼 + 基本面整合成一段散戶讀得懂的中文解讀。
              同一交易日的解讀會永久快取，不會重複扣 token。
            </p>
            <div>
              <button
                type="button"
                onClick={() => generate(false)}
                className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-[var(--brand-500)] hover:bg-[var(--brand-600)] text-white text-sm font-medium transition-colors"
              >
                <Icon name="psychology" size={16} />
                生成 AI 解讀
              </button>
            </div>
          </div>
        )}

        {loading && (
          <div className="flex items-center gap-2 text-sm text-[var(--text-secondary)]">
            <Icon name="hourglass_top" size={16} className="animate-pulse" />
            正在生成中（首次約 3–5 秒）⋯
          </div>
        )}

        {error && (
          <div className="flex flex-col gap-2 text-sm">
            <div className="text-[var(--color-down)] inline-flex items-center gap-2">
              <Icon name="error" size={16} filled />
              生成失敗
            </div>
            <code className="block text-xs font-mono text-[var(--text-tertiary)] bg-[var(--surface-2,_var(--surface))] px-2 py-1 rounded break-all">
              {error}
            </code>
            <button
              type="button"
              onClick={() => generate(false)}
              className="self-start text-xs px-3 py-1.5 rounded border border-[var(--border-default)] hover:bg-surface-hover"
            >
              重試
            </button>
          </div>
        )}

        {data && (
          <div className="flex flex-col gap-3">
            <div className="flex items-center gap-2 text-[11px] text-[var(--text-tertiary)]">
              <span>資料截至 {data.asOf}</span>
              <span>·</span>
              <span>{data.cached ? "快取命中" : "本次新生成"}</span>
            </div>
            <div className="text-[15px] leading-[1.85] text-[var(--text-primary)] whitespace-pre-wrap">
              {data.narrative}
            </div>
            <div className="flex items-center gap-3 text-[11px] text-[var(--text-tertiary)] pt-2 border-t border-[var(--border-subtle,_var(--border-default))]">
              <span>本內容由 LLM 生成，僅作為輔助解讀，不構成投資建議。</span>
              <button
                type="button"
                onClick={() => generate(true)}
                disabled={loading}
                className="ml-auto inline-flex items-center gap-1 text-[11px] px-2 py-1 rounded hover:bg-surface-hover disabled:opacity-50"
                title="跳過快取重新生成（會扣 token）"
              >
                <Icon name="refresh" size={12} />
                重新生成
              </button>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
