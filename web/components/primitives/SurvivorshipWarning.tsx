import { Icon } from "./Icon";

/**
 * 回測頁面通用的 survivorship-bias 揭示。
 * 系統的 universe 來自當前 daily_price 表，下市股不在裡面 → 回測結果系統性偏向倖存者。
 * Brown et al. (1992) 顯示這類 bias 可讓 long-only 策略年化偏高 1-3%。
 *
 * UIUX 審查：原本永遠展開太搶眼。改成 <details> 預設摺疊，使用者第一次看完之後不再被打擾。
 */
export function SurvivorshipWarning() {
  return (
    <details
      className="rounded-lg border border-[var(--info-border)] bg-[var(--info-bg)] text-[var(--info-fg)] text-xs"
      role="note"
    >
      <summary className="px-4 py-2.5 cursor-pointer inline-flex items-center gap-2 select-none">
        <Icon name="info" size={16} className="shrink-0" />
        <strong>Survivorship bias 提醒</strong>
        <span className="text-[var(--info-fg)] opacity-70">（點擊展開）</span>
      </summary>
      <div className="px-4 pb-3 pl-10 leading-relaxed">
        回測 universe 為「目前資料庫中仍有交易的股票」，<strong>下市股不在樣本內</strong>。
        這會讓 long-only 策略結果系統性偏向倖存者（學術上估計年化偏高 1-3%）。
        實盤前請對結果留 buffer，並避免把單檔極端報酬當做策略 alpha 的證據。
      </div>
    </details>
  );
}
