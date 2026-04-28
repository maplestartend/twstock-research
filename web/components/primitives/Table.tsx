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
  sticky = false,
}: {
  children?: React.ReactNode;
  align?: Align;
  className?: string;
  /** true → 該 cell 黏在 left:0（給「代號 / 名稱」首欄使用，mobile 橫捲時保持可見） */
  sticky?: boolean;
}) {
  return (
    <th
      className={cn(
        "h-10 px-4 font-medium text-[12px] tracking-wide text-[var(--text-secondary)]",
        ALIGN_CLS[align],
        // 配合 thead 的 bg-subtle 不洩漏右側捲動內容；z-index 比一般 row 高一階
        sticky && "sticky left-0 z-10 bg-subtle",
        className,
      )}
    >
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
  /** true → 該 cell 黏在 left:0；mobile 橫捲時首欄保持可見。
   *  注意：必須同時保留實心背景，否則右側 scroll 內容會透出。預設用 bg-surface。 */
  sticky?: boolean;
};

export function Td({
  children,
  align = "left",
  numeric = false,
  size = "comfortable",
  className,
  sticky = false,
}: TdProps) {
  return (
    <td
      className={cn(
        size === "compact" ? "h-12 px-4" : "h-14 px-4",
        ALIGN_CLS[align],
        numeric && "numeric",
        // sticky 配合 group-hover 時要連帶 hover 同色，避免捲動時首欄看起來脫離 row
        sticky && "sticky left-0 z-[1] bg-surface group-hover:bg-subtle",
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
