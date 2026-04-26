/**
 * 共用表格 cell：取代各頁本地定義的 Th / Td。
 *
 * 設計：
 * - Th 一律 h-10、表頭灰底由 thead 自己加
 * - Td 預設 size="comfortable" (h-14)，給主要 listing 頁（雷達/自選/持股/行事曆/歷史）使用
 * - Td size="compact" (h-12) 給結果表 / 工具表（回測明細、grid 結果、DQ、watchlist-manage 等密集資料）
 *
 * 把 align 統一為三向 (left/right/center)；numeric class 用於數字欄位（tabular-nums）。
 */
import { cn } from "@/lib/utils";

export type Align = "left" | "right" | "center";

const ALIGN_CLS: Record<Align, string> = {
  left: "text-left",
  right: "text-right",
  center: "text-center",
};

export function Th({
  children,
  align = "left",
  className,
}: {
  children?: React.ReactNode;
  align?: Align;
  className?: string;
}) {
  return (
    <th className={cn("h-10 px-4 font-medium", ALIGN_CLS[align], className)}>
      {children}
    </th>
  );
}

export type TdProps = {
  children?: React.ReactNode;
  align?: Align;
  numeric?: boolean;
  /** comfortable=h-14（listing 頁）；compact=h-12（結果/工具表） */
  size?: "comfortable" | "compact";
  className?: string;
};

export function Td({
  children,
  align = "left",
  numeric = false,
  size = "comfortable",
  className,
}: TdProps) {
  return (
    <td
      className={cn(
        size === "compact" ? "h-12 px-4" : "h-14 px-4",
        ALIGN_CLS[align],
        numeric && "numeric",
        className,
      )}
    >
      {children}
    </td>
  );
}

/** Compact 變體，等同 `<Td size="compact" {...props} />`。
 * 之前 7 個 page 各自寫 `const Td = (props) => <TdBase size="compact" {...props} />` 的 shim，
 * 統一抽出來省 dup。 */
export function TdCompact(props: Omit<TdProps, "size">) {
  return <Td {...props} size="compact" />;
}
