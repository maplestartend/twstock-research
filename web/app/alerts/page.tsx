import { apiGet, type AlertRule } from "@/lib/api";
import { PageHeader } from "@/components/primitives/PageHeader";
import { EmptyState } from "@/components/primitives/EmptyState";
import { Th, Td } from "@/components/primitives/Table";
import { TableContainer } from "@/components/primitives/TableContainer";
import { StockIdCell } from "@/components/primitives/StockIdCell";
import { BackendDownError } from "@/components/primitives/BackendDownError";
import { Icon } from "@/components/primitives/Icon";
import { AlertControls } from "./AlertControls";
import { AlertRuleForm } from "./AlertRuleForm";

export const dynamic = "force-dynamic";

const KIND_LABEL: Record<string, { label: string; description: string }> = {
  price_below: { label: "價格跌破", description: "現價 ≤ 設定值" },
  price_above: { label: "價格突破", description: "現價 ≥ 設定值" },
  score_drop: { label: "短期分數下跌", description: "近 7 日短期分數跌幅 ≥ 閾值" },
  score_rise: { label: "短期分數上升", description: "近 7 日短期分數漲幅 ≥ 閾值" },
  atr_breached: { label: "ATR 跌破", description: "持股觸發 ATR 動態停損" },
};

export default async function AlertsPage() {
  let rules: AlertRule[];
  try {
    rules = await apiGet<AlertRule[]>("/api/alerts/rules");
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
        <h2 className="text-base font-semibold inline-flex items-center gap-2">
          <Icon name="rule" size={20} className="text-[var(--brand-500)]" />
          已設定規則
          <span className="numeric text-xs text-[var(--text-tertiary)] font-normal ml-2">
            {rules.length} 條（{rules.filter((r) => r.active).length} 活躍）
          </span>
        </h2>
        {rules.length === 0 ? (
          <EmptyState size="sm">
            還沒設過任何預警。用上方表單加一條開始（例：2330 跌破 800、0050 突破 200）。
          </EmptyState>
        ) : (
          <TableContainer>
            <table className="w-full text-[15px] min-w-[860px]">
              <thead className="bg-subtle">
                <tr>
                  <Th align="center" className="w-[80px]">狀態</Th>
                  <Th sticky className="w-[160px]">代號</Th>
                  <Th className="w-[140px]">類型</Th>
                  <Th align="right" className="w-[100px]">閾值</Th>
                  <Th>備註 / 上次觸發</Th>
                  <Th align="center" className="w-[120px]">操作</Th>
                </tr>
              </thead>
              <tbody>
                {rules.map((r) => {
                  const meta = KIND_LABEL[r.ruleKind];
                  return (
                    <tr key={r.id} className="group border-t border-[var(--border-default)] hover:bg-subtle">
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
