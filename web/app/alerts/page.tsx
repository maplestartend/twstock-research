import { apiGet, type AlertRule, type AlertRuleKind } from "@/lib/api";
import { PageHeader } from "@/components/primitives/PageHeader";
import { EmptyState } from "@/components/primitives/EmptyState";
import { Th, Td } from "@/components/primitives/Table";
import { TableContainer } from "@/components/primitives/TableContainer";
import { StockIdCell } from "@/components/primitives/StockIdCell";
import { BackendDownError } from "@/components/primitives/BackendDownError";
import { Icon } from "@/components/primitives/Icon";
import { AlertControls } from "./AlertControls";
import { AlertRuleForm } from "./AlertRuleForm";
import { EvaluateButton } from "./EvaluateButton";

export const dynamic = "force-dynamic";

const KIND_LABEL: Record<string, { label: string; description: string }> = {
  price_below: { label: "價格跌破", description: "現價 ≤ 設定值" },
  price_above: { label: "價格突破", description: "現價 ≥ 設定值" },
  score_drop: { label: "短期分數下跌", description: "近 7 日短期分數跌幅 ≥ 閾值" },
  score_rise: { label: "短期分數上升", description: "近 7 日短期分數漲幅 ≥ 閾值" },
  atr_breached: { label: "ATR 跌破", description: "持股觸發 ATR 動態停損" },
};

function formatActual(kind: AlertRuleKind, v: number): string {
  if (kind === "score_drop" || kind === "score_rise") {
    return `${v >= 0 ? "+" : ""}${v.toFixed(1)} 分`;
  }
  return v.toLocaleString("zh-TW", { maximumFractionDigits: 2 });
}

/** 距離觸發的可讀文字。回 null 表示這個 rule kind 不適用距離概念（atr_breached）。 */
function distanceLabel(kind: AlertRuleKind, actual: number, threshold: number | null): string | null {
  if (threshold == null) return null;
  if (kind === "price_below") {
    const pct = ((actual - threshold) / threshold) * 100;
    return `${pct >= 0 ? "+" : ""}${pct.toFixed(1)}% 離 ${threshold}`;
  }
  if (kind === "price_above") {
    const pct = ((threshold - actual) / threshold) * 100;
    return `${pct >= 0 ? "+" : ""}${pct.toFixed(1)}% 離 ${threshold}`;
  }
  if (kind === "score_drop") {
    // actual = signed delta；threshold = 跌幅門檻 (正數)。觸發要 -actual >= threshold。
    const remaining = threshold + actual;
    return remaining > 0 ? `還要再跌 ${remaining.toFixed(1)} 分` : "已超過門檻";
  }
  if (kind === "score_rise") {
    const remaining = threshold - actual;
    return remaining > 0 ? `還要再漲 ${remaining.toFixed(1)} 分` : "已超過門檻";
  }
  return null;
}

function ActualCell({ rule }: { rule: AlertRule }) {
  if (!rule.active) {
    return <span className="text-[var(--text-tertiary)] text-xs">— 暫停</span>;
  }
  if (rule.actualValue == null) {
    return <span className="text-[var(--text-tertiary)] text-xs">資料不足</span>;
  }
  const dist = distanceLabel(rule.ruleKind, rule.actualValue, rule.threshold);
  if (rule.triggered) {
    return (
      <div className="flex flex-col gap-0.5 items-end">
        <span className="numeric font-medium text-[var(--color-down)]">
          {formatActual(rule.ruleKind, rule.actualValue)}
        </span>
        <span className="text-[10px] inline-flex items-center gap-1 text-[var(--color-down)] font-medium">
          <Icon name="error" size={12} /> 已觸發
        </span>
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-0.5 items-end">
      <span className="numeric font-medium">{formatActual(rule.ruleKind, rule.actualValue)}</span>
      {dist && <span className="text-[10px] text-[var(--text-tertiary)]">{dist}</span>}
    </div>
  );
}

export default async function AlertsPage() {
  let rules: AlertRule[];
  try {
    // noCache: true — 使用者會主動新增/刪除/啟停規則，預設 60s 快取會讓 router.refresh()
    // 拿不到新狀態，得 Ctrl+F5 才看得到變化。watchlist/holdings 也是同樣理由用 noCache。
    rules = await apiGet<AlertRule[]>("/api/alerts/rules", { noCache: true });
  } catch (e) {
    return <BackendDownError error={e} pageTitle="預警" />;
  }

  return (
    <div className="p-4 lg:p-8 flex flex-col gap-6 max-w-[1200px] mx-auto">
      <PageHeader
        title="預警"
        icon="notifications_active"
        description="條件成立時推 Discord 通知。同一條規則 24 小時冷卻避免刷屏；改 active=false 暫停而非刪除。"
        extra="預警評估排在 daily-update 結尾，跟 snapshot 同步。"
      />

      <AlertRuleForm />

      <section className="flex flex-col gap-3">
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <h2 className="text-base font-semibold inline-flex items-center gap-2">
            <Icon name="rule" size={20} className="text-[var(--brand-500)]" />
            已設定規則
            <span className="numeric text-xs text-[var(--text-tertiary)] font-normal ml-2">
              {rules.length} 條（{rules.filter((r) => r.active).length} 活躍）
            </span>
          </h2>
          <EvaluateButton />
        </div>
        {rules.length === 0 ? (
          <EmptyState size="sm">
            還沒設過任何預警。用上方表單加一條開始（例：2330 跌破 800、0050 突破 200）。
          </EmptyState>
        ) : (
          <TableContainer>
            <table className="w-full text-[15px] min-w-[980px]">
              <thead className="bg-subtle">
                <tr>
                  <Th align="center" className="w-[80px]">狀態</Th>
                  <Th sticky className="w-[160px]">代號</Th>
                  <Th className="w-[140px]">類型</Th>
                  <Th align="right" className="w-[100px]">閾值</Th>
                  <Th align="right" className="w-[160px]">現值 / 距離</Th>
                  <Th>備註 / 上次觸發</Th>
                  <Th align="center" className="w-[120px]">操作</Th>
                </tr>
              </thead>
              <tbody>
                {rules.map((r) => {
                  const meta = KIND_LABEL[r.ruleKind];
                  const rowCls = r.triggered
                    ? "group border-t border-[var(--color-down-border)] bg-[var(--color-down-bg)]/40 hover:bg-[var(--color-down-bg)]/60"
                    : "group border-t border-[var(--border-default)] hover:bg-subtle";
                  return (
                    <tr key={r.id} className={rowCls}>
                      <Td align="center">
                        <span
                          className={
                            r.active
                              ? "inline-block w-2 h-2 rounded-full bg-[var(--color-up)]"
                              : "inline-block w-2 h-2 rounded-full bg-[var(--text-tertiary)]"
                          }
                          title={r.active ? "啟用中" : "暫停"}
                        />
                      </Td>
                      <Td sticky>
                        <StockIdCell stockId={r.stockId} />
                      </Td>
                      <Td>
                        <div className="flex flex-col gap-0.5">
                          <span className="font-medium text-[var(--text-primary)]">{meta?.label ?? r.ruleKind}</span>
                          <span className="text-[11px] text-[var(--text-tertiary)]">{meta?.description ?? ""}</span>
                        </div>
                      </Td>
                      <Td align="right" numeric>
                        {r.threshold == null ? (
                          <span className="text-[var(--text-tertiary)]">—</span>
                        ) : (
                          r.threshold.toLocaleString("zh-TW", { maximumFractionDigits: 2 })
                        )}
                      </Td>
                      <Td align="right">
                        <ActualCell rule={r} />
                      </Td>
                      <Td>
                        <div className="flex flex-col gap-0.5 text-xs">
                          <span className="text-[var(--text-secondary)]">{r.note ?? "—"}</span>
                          {r.lastTriggeredAt && (
                            <span className="text-[var(--text-tertiary)]">
                              上次觸發：{r.lastTriggeredAt.replace("T", " ").slice(0, 16)}
                            </span>
                          )}
                        </div>
                      </Td>
                      <Td align="center">
                        <AlertControls rule={r} />
                      </Td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </TableContainer>
        )}
      </section>
    </div>
  );
}
