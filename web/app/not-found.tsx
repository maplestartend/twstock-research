import Link from "next/link";
import { Icon } from "@/components/primitives/Icon";

export default function NotFound() {
  return (
    <div className="p-8 max-w-[640px] mx-auto">
      <div className="rounded-xl border border-[var(--border-default)] bg-surface p-6 flex gap-4 items-start">
        <Icon name="search_off" size={32} filled className="text-[var(--text-tertiary)] shrink-0" />
        <div className="flex-1">
          <h1 className="text-[22px] font-bold text-[var(--text-primary)]">找不到頁面</h1>
          <p className="text-sm text-[var(--text-secondary)] mt-2">
            這個網址不存在或已被移除。常見原因：股票代號打錯、舊的書籤、或剛拆掉的舊頁面。
          </p>
          <div className="mt-4 flex gap-3">
            <Link
              href="/"
              className="inline-flex items-center gap-2 h-9 px-4 rounded-md bg-[var(--brand-500)] text-white text-sm font-medium hover:bg-[var(--brand-600)] transition-colors"
            >
              <Icon name="home" size={16} />
              回首頁
            </Link>
            <Link
              href="/radar"
              className="inline-flex items-center gap-2 h-9 px-4 rounded-md border border-[var(--border-default)] bg-surface text-sm hover:bg-subtle transition-colors"
            >
              <Icon name="radar" size={16} />
              雷達掃描
            </Link>
          </div>
        </div>
      </div>
    </div>
  );
}
