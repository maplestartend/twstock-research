import { Icon } from "./Icon";
import { humanizeApiError } from "@/lib/api";

/**
 * 後端 API 連不上時顯示的友善錯誤畫面（取代直接 throw → 整頁 500 白屏）。
 *
 * 在 Server Component 用法：
 *   try { const data = await apiGet(...) } catch (e) { return <BackendDownError error={e} />; }
 */
export function BackendDownError({ error, pageTitle }: { error: unknown; pageTitle?: string }) {
  const msg = humanizeApiError(error);
  return (
    <div className="p-8 max-w-[800px] mx-auto">
      {pageTitle && (
        <h1 className="text-[22px] font-bold text-[var(--text-primary)] mb-6">
          {pageTitle}
        </h1>
      )}
      <div className="rounded-xl border border-[var(--error-border)] bg-[var(--error-bg)] p-6 flex gap-4 items-start">
        <Icon name="cloud_off" size={32} filled className="text-[var(--error-fg)] shrink-0" />
        <div className="flex-1 min-w-0">
          <h2 className="font-semibold text-[var(--error-fg)]">無法載入資料</h2>
          <p className="text-sm text-[var(--error-fg)] mt-2 leading-relaxed">{msg}</p>
          <ul className="text-xs text-[var(--error-fg)] mt-3 space-y-1 list-disc pl-5">
            <li>確認 FastAPI 視窗有開（雙擊 <code className="font-mono px-1 py-0.5 bg-surface/50 rounded">launch.bat</code>）</li>
            <li>檢查 port 8000 是否被其他程式佔用</li>
            <li>若剛清過資料，先跑 <code className="font-mono px-1 py-0.5 bg-surface/50 rounded">python -m scripts.market_update --days 60</code> 補資料</li>
          </ul>
        </div>
      </div>
    </div>
  );
}
