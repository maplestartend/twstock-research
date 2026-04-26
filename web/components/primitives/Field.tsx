/**
 * 表單欄位 wrapper：取代各回測頁本地定義的 Field。
 *
 * 支援：
 * - label：必填，欄位文字
 * - hint：手動 override 提示文字
 * - term：詞彙表 key，會帶 InfoTip 並用詞彙短解作為 hint fallback
 * - className：套到外層 label
 */
import { InfoTip } from "@/components/primitives/InfoTip";
import { getTerm, type TermKey } from "@/lib/terms";
import { cn } from "@/lib/utils";

export function Field({
  label,
  hint,
  term,
  children,
  className,
}: {
  label: string;
  hint?: string;
  term?: TermKey;
  children: React.ReactNode;
  className?: string;
}) {
  const def = term ? getTerm(term) : null;
  const finalHint = hint ?? def?.short;
  return (
    <label className={cn("flex flex-col gap-1.5", className)}>
      <span className="text-xs text-[var(--text-secondary)] font-medium inline-flex items-center gap-1">
        {label}
        {term && <InfoTip term={term} />}
      </span>
      {children}
      {finalHint && (
        <span className="text-[10px] text-[var(--text-tertiary)]">{finalHint}</span>
      )}
    </label>
  );
}
