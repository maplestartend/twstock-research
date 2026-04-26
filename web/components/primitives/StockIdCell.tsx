import Link from "next/link";

/**
 * 表格內共用的「代號 / 名稱」cell。
 * - 代號：tabular-nums + 主要文字色 + semibold
 * - 名稱：12px secondary（比 tertiary 對比強，小字仍易讀），truncate 防溢位
 *
 * 12 個 listing 表 + HoldingsTable 之前各自貼了一份相同 markup，這支抽出來收掉。
 */
export function StockIdCell({
  stockId,
  stockName,
}: {
  stockId: string;
  stockName?: string | null;
}) {
  return (
    <Link href={`/stocks/${stockId}`} className="flex flex-col hover:underline">
      <span className="numeric font-semibold text-[var(--text-primary)]">{stockId}</span>
      {stockName ? (
        <span className="text-[var(--text-secondary)] text-[12px] truncate">{stockName}</span>
      ) : null}
    </Link>
  );
}
