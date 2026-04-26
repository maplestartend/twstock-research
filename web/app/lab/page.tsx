import Link from "next/link";
import { Icon } from "@/components/primitives/Icon";
import { PageHeader } from "@/components/primitives/PageHeader";

export const metadata = { title: "回測工具室 — 台股研究儀表板" };

type Tool = {
  href: string;
  icon: string;
  title: string;
  description: string;
  hint: string;
};

const TOOLS: Tool[] = [
  {
    href: "/backtest",
    icon: "replay",
    title: "策略回測",
    description: "把分數規則套到單檔股票過去走勢上，看歷年實際進出表現。",
    hint: "適合：先驗證單一標的的策略可行性",
  },
  {
    href: "/portfolio-backtest",
    icon: "bar_chart",
    title: "投組回測",
    description: "同一套規則跑整個自選股池或產業池，輸出投組曲線、走勢、KPI。",
    hint: "適合：看策略整體能不能打贏買入持有",
  },
  {
    href: "/grid-search",
    icon: "science",
    title: "參數掃描",
    description: "對進/出場門檻、停損停利等參數做網格搜索，找出最佳組合。",
    hint: "適合：策略大致 work 後再來精調",
  },
  {
    href: "/event-backtest",
    icon: "celebration",
    title: "除權息回測",
    description: "驗證「除權息前進場」這類事件型策略，含填權息與隔離時段勝率。",
    hint: "適合：研究現金股利／殖利率事件",
  },
  {
    href: "/weight-tuner",
    icon: "tune",
    title: "權重調優",
    description: "從歷年雷達命中表現反推各分數欄位的最佳加權，直接覆蓋自訂預設。",
    hint: "適合：覺得內建權重不準時用",
  },
];

export default function LabHub() {
  return (
    <div className="p-4 lg:p-8 flex flex-col gap-8 max-w-[1400px] mx-auto">
      <PageHeader
        title="回測工具室"
        icon="science"
        description="所有回測 / 參數搜索 / 權重調優工具集中處。每張卡進到對應頁面實作。"
      />

      <section className="rounded-xl bg-[var(--info-bg)] border border-[var(--info-border)] px-4 py-3 text-sm text-[var(--info-fg)] flex items-start gap-2">
        <Icon name="info" size={16} filled className="mt-0.5 shrink-0" />
        <p className="leading-relaxed">
          策略 → 投組 → 參數 → 權重，是常見研究流程。先確認單檔可行 → 再放到投組池 → 找最佳參數 → 反推權重。
        </p>
      </section>

      <section className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {TOOLS.map((t) => (
          <Link
            key={t.href}
            href={t.href}
            className="group rounded-xl border border-[var(--border-default)] bg-surface p-5 hover:border-[var(--brand-500)] hover:bg-[var(--brand-tint-soft)] transition-colors flex flex-col gap-3"
          >
            <div className="flex items-start gap-3">
              <span className="inline-flex items-center justify-center w-10 h-10 rounded-lg bg-[var(--brand-tint)] text-[var(--brand-600)] dark:text-[var(--brand-400)] shrink-0">
                <Icon name={t.icon} size={22} filled />
              </span>
              <div className="flex-1 min-w-0">
                <div className="text-base font-semibold text-[var(--text-primary)] inline-flex items-center gap-1.5">
                  {t.title}
                  <Icon name="chevron_right" size={18} className="text-[var(--text-tertiary)] group-hover:text-[var(--brand-500)] group-hover:translate-x-0.5 transition-transform" />
                </div>
                <p className="text-xs text-[var(--text-tertiary)] mt-1 leading-relaxed">{t.description}</p>
              </div>
            </div>
            <div className="text-[11px] text-[var(--text-tertiary)] border-t border-[var(--border-default)] pt-2">
              {t.hint}
            </div>
          </Link>
        ))}
      </section>
    </div>
  );
}
