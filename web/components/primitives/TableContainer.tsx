import { cn } from "@/lib/utils";
import { TableScrollHint } from "./TableScrollHint";

/**
 * 共用表格外殼：取代 16 個 page 各自寫的
 *   <div className="rounded-xl border border-... bg-surface overflow-x-auto"> ... </div>
 * 包裝。
 *
 * 預設外層加 <TableScrollHint>（手機看到右側箭頭知道可捲）。
 * 不需要可關掉 (`scrollHint={false}`)，例如表格本來就能完整顯示沒有 overflow 的 case。
 */
export function TableContainer({
  children,
  scrollHint = true,
  className,
}: {
  children: React.ReactNode;
  scrollHint?: boolean;
  className?: string;
}) {
  const inner = (
    <div
      data-scroll
      className={cn(
        "rounded-xl border border-[var(--border-default)] bg-surface overflow-x-auto",
        className,
      )}
    >
      {children}
    </div>
  );
  return scrollHint ? <TableScrollHint>{inner}</TableScrollHint> : inner;
}
