/**
 * Excel (.xlsx) 下載按鈕。
 *
 * 與 CSV 不同：xlsx 是二進位、含格式化 → 必須由後端 openpyxl 產出，前端用標準
 * `<a download>` 觸發下載即可。瀏覽器看到 Content-Disposition: attachment 會自動
 * 存檔，不會在 tab 中嘗試打開。
 *
 * 為什麼不重用 DownloadCsvButton：
 * - CSV 從前端已 fetch 的 array 即時組裝（純 client-side blob）
 * - XLSX 必須打 API 重新產生（後端做 styling/formula）
 * - 兩者的「按一次」語意不同 → 分開兩個 component 比 conditional logic 清楚
 *
 * 此 component 是 SSR-safe（純 anchor，無 hooks），可在 RSC 中直接 render。
 */
import { Icon } from "./Icon";
import { btnSecondary } from "@/lib/formClasses";
import { cn } from "@/lib/utils";

export function DownloadXlsxButton({
  href,
  label = "下載 Excel",
  size = "md",
  disabled,
}: {
  /** API URL（含 query string）；後端應回 application/vnd.openxmlformats-officedocument.spreadsheetml.sheet */
  href: string;
  label?: string;
  size?: "sm" | "md";
  disabled?: boolean;
}) {
  if (disabled) {
    return (
      <button
        type="button"
        disabled
        title="無資料可下載"
        className={cn(btnSecondary, size === "sm" && "h-8 px-2.5 text-xs")}
      >
        <Icon name="download" size={size === "sm" ? 14 : 16} />
        {label}
      </button>
    );
  }
  return (
    <a
      href={href}
      // download 屬性讓瀏覽器強制觸發下載（即使 server 漏設 Content-Disposition）；
      // 同 origin 才有效，跨 origin 會被忽略。本專案 API 跟前端同 origin（rewrite proxy）。
      download
      className={cn(btnSecondary, size === "sm" && "h-8 px-2.5 text-xs", "no-underline")}
    >
      <Icon name="download" size={size === "sm" ? 14 : 16} />
      {label}
    </a>
  );
}
