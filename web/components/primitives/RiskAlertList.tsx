import { cn } from "@/lib/utils";
import { Icon } from "./Icon";
import type { RiskAlert } from "@/lib/api";

const STYLE = {
  critical: {
    bg: "bg-[var(--color-up-bg)] border-[var(--color-up-border)]",
    fg: "text-[var(--color-up)]",
    icon: "error",
  },
  warning: {
    bg: "bg-[var(--warning-bg)] border-[var(--warning-border)]",
    fg: "text-[var(--warning-fg)]",
    icon: "warning",
  },
  info: {
    bg: "bg-[var(--info-bg)] border-[var(--info-border)]",
    fg: "text-[var(--info-fg)]",
    icon: "info",
  },
} as const;

const SEVERITY_ORDER: ("critical" | "warning" | "info")[] = ["critical", "warning", "info"];

/** 把多筆同 severity 的 RiskAlert 合成一張卡（list 列點），避免 4 張集中度提醒 2x2 排成噪音。 */
export function RiskAlertList({ alerts }: { alerts: RiskAlert[] }) {
  if (!alerts || alerts.length === 0) return null;
  const grouped: Record<string, RiskAlert[]> = {};
  for (const a of alerts) {
    (grouped[a.severity] ??= []).push(a);
  }
  return (
    <div className="flex flex-col gap-2">
      {SEVERITY_ORDER.filter((sev) => grouped[sev]?.length).map((sev) => {
        const list = grouped[sev]!;
        const s = STYLE[sev];
        return (
          <div key={sev} className={cn("rounded-lg border p-3", s.bg)}>
            <div className="flex items-center gap-2 mb-2">
              <span className={cn("inline-flex items-center justify-center w-6 h-6 rounded-full bg-surface", s.fg)}>
                <Icon name={s.icon} size={16} filled />
              </span>
              <span className={cn("text-sm font-semibold", s.fg)}>
                {sev === "critical" ? "嚴重" : sev === "warning" ? "警告" : "提示"} · {list.length} 項
              </span>
            </div>
            <ul className="flex flex-col gap-1.5 ml-8">
              {list.map((a, i) => (
                <li key={`${a.severity}-${a.title}-${i}`} className="text-sm text-[var(--text-primary)]">
                  <span className="font-medium">{a.title}</span>
                  <span className="text-[var(--text-secondary)] ml-1.5">— {a.description}</span>
                </li>
              ))}
            </ul>
          </div>
        );
      })}
    </div>
  );
}
