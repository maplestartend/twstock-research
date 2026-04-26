"use client";

import { useDeferredValue, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { Icon } from "@/components/primitives/Icon";
import { InfoTip } from "@/components/primitives/InfoTip";
import { ScoreBadge } from "@/components/primitives/ScoreBadge";
import { NextStepCards } from "@/components/primitives/NextStepCard";
import { Th, Td } from "@/components/primitives/Table";
import { fmtPrice, toneClass } from "@/lib/format";
import { PART_LABEL } from "@/lib/labels";
import { TERMS, type TermKey } from "@/lib/terms";
import { cn } from "@/lib/utils";
import {
  apiDelete,
  apiGet,
  apiPost,
  humanizeApiError,
  type BuiltinPreset,
  type PresetListResponse,
  type TunerBreakdownResponse,
  type UserPreset,
  type VisibleKeysResponse,
  type WeightSet,
} from "@/lib/api";

/** 加權平均：Σ(parts[k] × weights[k]) / Σ(weights[k])；忽略 null parts、以剩下的權重重新歸一化 */
function weightedScore(parts: Record<string, number | null>, weights: Record<string, number>): number | null {
  let sum = 0;
  let wtotal = 0;
  for (const [k, v] of Object.entries(parts)) {
    if (v == null || !Number.isFinite(v)) continue;
    const w = weights[k] ?? 0;
    if (w > 0) {
      sum += v * w;
      wtotal += w;
    }
  }
  return wtotal > 0 ? sum / wtotal : null;
}

type Mode = "beginner" | "advanced";
const MODE_KEY = "weightTuner.mode";

export function WeightTunerClient({
  data,
  initialPresets,
  visibleKeys,
}: {
  data: TunerBreakdownResponse;
  initialPresets: PresetListResponse;
  visibleKeys: VisibleKeysResponse;
}) {
  const [shortW, setShortW] = useState<Record<string, number>>(() => ({ ...data.defaultWeights.short }));
  const [midW, setMidW] = useState<Record<string, number>>(() => ({ ...data.defaultWeights.mid }));
  const [longW, setLongW] = useState<Record<string, number>>(() => ({ ...data.defaultWeights.long }));
  const [presets, setPresets] = useState<PresetListResponse>(initialPresets);
  const [activePreset, setActivePreset] = useState<string>("default");  // 顯示「目前套用了哪個 preset」
  const [mode, setMode] = useState<Mode>("advanced");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // 載入儲存的 mode 偏好
  useEffect(() => {
    const saved = typeof window !== "undefined" ? window.localStorage.getItem(MODE_KEY) : null;
    if (saved === "beginner" || saved === "advanced") setMode(saved);
  }, []);
  useEffect(() => {
    if (typeof window !== "undefined") window.localStorage.setItem(MODE_KEY, mode);
  }, [mode]);

  const resetAll = () => {
    setShortW({ ...data.defaultWeights.short });
    setMidW({ ...data.defaultWeights.mid });
    setLongW({ ...data.defaultWeights.long });
    setActivePreset("default");
  };

  const applyPreset = (w: WeightSet, name: string) => {
    setShortW({ ...w.short });
    setMidW({ ...w.mid });
    setLongW({ ...w.long });
    setActivePreset(name);
    setErr(null);
  };

  const refreshPresets = async () => {
    try {
      const fresh = await apiGet<PresetListResponse>("/api/weight-tuner/presets", { noCache: true });
      setPresets(fresh);
    } catch {
      // 靜默失敗：清單沒更新但操作已發出
    }
  };

  const savePreset = async () => {
    const name = window.prompt("請輸入這組權重的名稱（例：我的籌碼追擊）");
    if (!name || !name.trim()) return;
    const description = window.prompt("簡短描述（可留空）") ?? "";
    setBusy(true); setErr(null);
    try {
      const saved = await apiPost<UserPreset>("/api/weight-tuner/presets", {
        name: name.trim(),
        description,
        weights: { short: shortW, mid: midW, long: longW },
      });
      await refreshPresets();
      setActivePreset(saved.name);
    } catch (e) {
      setErr(humanizeApiError(e));
    } finally {
      setBusy(false);
    }
  };

  const deletePreset = async (name: string) => {
    if (!window.confirm(`確定刪除 preset「${name}」？此動作無法復原。`)) return;
    setBusy(true); setErr(null);
    try {
      await apiDelete(`/api/weight-tuner/presets/${encodeURIComponent(name)}`);
      await refreshPresets();
      if (activePreset === name) resetAll();
    } catch (e) {
      setErr(humanizeApiError(e));
    } finally {
      setBusy(false);
    }
  };

  // 用 useDeferredValue 把 30 檔 × 19 子指標的重算延遲到滑桿停下後
  // 滑桿仍走 controlled input 即時更新（顯示的數字會跟手指走），
  // 但下方對比表只在 idle frame 才重算，避免拖拉時掉 frame。
  const deferredShortW = useDeferredValue(shortW);
  const deferredMidW = useDeferredValue(midW);
  const deferredLongW = useDeferredValue(longW);

  const rows = useMemo(() => data.stocks.map((s) => {
    const newShort = weightedScore(s.shortParts, deferredShortW);
    const newMid = weightedScore(s.midParts, deferredMidW);
    const newLong = weightedScore(s.longParts, deferredLongW);
    // composite：三維中只要有 None 就跳過該維、重新歸一化（與後端 composite_score 一致）
    const dimW = { short: 0.3, mid: 0.5, long: 0.2 };
    let used = 0, acc = 0;
    if (newShort != null) { acc += newShort * dimW.short; used += dimW.short; }
    if (newMid != null)   { acc += newMid   * dimW.mid;   used += dimW.mid;   }
    if (newLong != null)  { acc += newLong  * dimW.long;  used += dimW.long;  }
    const newComposite = used > 0 ? acc / used : null;
    const defComposite = s.compositeDefault;
    return { ...s, newShort, newMid, newLong, newComposite, defComposite };
  }).sort((a, b) => (b.newComposite ?? -1) - (a.newComposite ?? -1)), [data.stocks, deferredShortW, deferredMidW, deferredLongW]);

  return (
    <>
      {/* Preset bar */}
      <PresetBar
        presets={presets}
        activeName={activePreset}
        onApply={applyPreset}
        onDelete={deletePreset}
        busy={busy}
      />

      {/* 模式切換 */}
      <div className="flex items-center gap-3 flex-wrap">
        <span className="text-xs text-[var(--text-tertiary)]">顯示模式：</span>
        <ModeToggle mode={mode} setMode={setMode} />
        <span className="text-[11px] text-[var(--text-tertiary)]">
          {mode === "beginner"
            ? "只顯示影響度最大的 5~6 個指標；其他用預設值。"
            : "顯示全部 19 個子指標。"}
        </span>
        <span className="text-xs text-[var(--text-tertiary)] ml-auto">綜合 = 0.5 × 中期 + 0.3 × 短期 + 0.2 × 長期</span>
      </div>

      {/* 權重面板 */}
      <section className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        <WeightPanel title="短期" tone="up" weights={shortW} setWeights={(w) => { setShortW(w); setActivePreset("custom"); }} defaults={data.defaultWeights.short} visibleKeys={mode === "beginner" ? visibleKeys.short : null} />
        <WeightPanel title="中期" tone="neutral" weights={midW} setWeights={(w) => { setMidW(w); setActivePreset("custom"); }} defaults={data.defaultWeights.mid} visibleKeys={mode === "beginner" ? visibleKeys.mid : null} />
        <WeightPanel title="長期" tone="down" weights={longW} setWeights={(w) => { setLongW(w); setActivePreset("custom"); }} defaults={data.defaultWeights.long} visibleKeys={mode === "beginner" ? visibleKeys.long : null} />
      </section>

      {/* 操作列 */}
      <div className="flex items-center gap-3 flex-wrap">
        <button onClick={resetAll} className={btnSecondary} disabled={busy}>
          <Icon name="refresh" size={16} />全部重設為預設
        </button>
        <button onClick={savePreset} className={btnPrimary} disabled={busy}>
          <Icon name="bookmark_add" size={16} />儲存為我的 Preset
        </button>
        {err && <span className="text-xs text-[var(--color-down)]">{err}</span>}
      </div>

      {/* 對比表 */}
      <section className="flex flex-col gap-3">
        <h2 className="text-base font-semibold inline-flex items-center gap-2">
          <Icon name="compare_arrows" size={20} className="text-[var(--brand-500)]" />
          自選股 評分對比（新權重 vs 預設）
          <span className="numeric text-xs text-[var(--text-tertiary)] font-normal ml-2">共 {rows.length} 檔</span>
        </h2>
        <div className="rounded-xl border border-[var(--border-default)] bg-surface overflow-x-auto">
          <table className="w-full text-sm min-w-[900px]">
            <thead className="bg-subtle">
              <tr className="text-[11px] uppercase tracking-wide text-[var(--text-secondary)]">
                <Th>代號 / 名稱</Th>
                <Th align="right">收盤</Th>
                <ThGroup>短期</ThGroup>
                <ThGroup>中期</ThGroup>
                <ThGroup>長期</ThGroup>
                <Th align="right">綜合（新）</Th>
                <Th align="right">Δ 綜合</Th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const compDelta = r.newComposite != null && r.defComposite != null
                  ? r.newComposite - r.defComposite
                  : null;
                return (
                  <tr key={r.stockId} className="border-t border-[var(--border-default)] hover:bg-subtle">
                    <Td>
                      <Link href={`/stocks/${r.stockId}`} className="flex flex-col hover:underline">
                        <span className="numeric font-semibold">{r.stockId}</span>
                        <span className="text-[var(--text-tertiary)] text-xs">{r.stockName}</span>
                      </Link>
                    </Td>
                    <Td align="right" numeric>{fmtPrice(r.close)}</Td>
                    <TdDim oldV={r.shortDefault} newV={r.newShort} />
                    <TdDim oldV={r.midDefault} newV={r.newMid} />
                    <TdDim oldV={r.longDefault} newV={r.newLong} />
                    <Td align="right">
                      <div className="flex justify-end">
                        <ScoreBadge score={r.newComposite} size="md" horizon="composite" />
                      </div>
                    </Td>
                    <Td align="right" numeric>
                      {compDelta == null ? (
                        <span className="text-[var(--text-tertiary)]">—</span>
                      ) : (
                        <span className={cn("font-semibold", tc(compDelta))}>
                          {compDelta > 0 ? "+" : ""}{compDelta.toFixed(1)}
                        </span>
                      )}
                    </Td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <p className="text-xs text-[var(--text-tertiary)]">
          Δ 綜合：新權重算出的綜合分數 − 預設權重的綜合分數。想固化這組權重，用「複製權重 JSON」貼回 <code className="font-mono">app/scoring/rubric.py</code>。
        </p>
      </section>

      <NextStepCards items={[
        {
          href: "/portfolio-backtest",
          icon: "verified",
          title: "驗證新權重的實戰表現",
          description: "改完權重後，回去用投組回測看新評分能不能在歷史資料賺錢。",
        },
        {
          href: "/radar",
          icon: "radar",
          title: "套用後找新候選股",
          description: "雷達掃描會用 rubric.py 的權重；固化後 → 雷達會自動用新標準。",
        },
        {
          href: "/grid-search",
          icon: "tune",
          title: "順便找最佳進出場參數",
          description: "權重和進出場門檻可以一起調，用 grid-search 找配合新權重的最佳參數組合。",
        },
      ]} />
    </>
  );
}

function WeightPanel({
  title, tone: toneName, weights, setWeights, defaults, visibleKeys,
}: {
  title: string;
  tone: "up" | "down" | "neutral";
  weights: Record<string, number>;
  setWeights: (w: Record<string, number>) => void;
  defaults: Record<string, number>;
  visibleKeys: string[] | null;   // null = 顯示全部；非空陣列 = 只顯示這些鍵
}) {
  const titleTone = toneName === "up" ? "text-[var(--color-up)]" : toneName === "down" ? "text-[var(--color-down)]" : "text-[var(--text-primary)]";
  const sum = Object.values(weights).reduce((a, b) => a + b, 0);
  const dirty = Object.entries(weights).some(([k, v]) => Math.abs(v - (defaults[k] ?? 0)) > 1e-6);
  const allEntries = Object.entries(weights);
  const visibleSet = visibleKeys && visibleKeys.length > 0 ? new Set(visibleKeys) : null;
  const shown = visibleSet ? allEntries.filter(([k]) => visibleSet.has(k)) : allEntries;
  const hidden = visibleSet ? allEntries.length - shown.length : 0;

  return (
    <div className="rounded-xl border border-[var(--border-default)] bg-surface p-4 flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <h3 className={cn("text-sm font-semibold inline-flex items-center gap-1", titleTone)}>
          {title}（{allEntries.length} 維）
          <InfoTip text="這個維度內所有指標權重會自動歸一化，不用湊到剛好 1.0；數字代表「相對其他指標」的影響度。" />
        </h3>
        <span className="text-[11px] text-[var(--text-tertiary)] numeric" title="總和會自動歸一化（不需要剛好 1.0）">總和 {sum.toFixed(2)}</span>
      </div>
      <div className="flex flex-col gap-2.5">
        {shown.map(([k, v]) => {
          const isTerm = (k in TERMS);
          return (
            <label key={k} className="flex flex-col gap-1">
              <div className="flex items-baseline justify-between text-xs">
                <span className="text-[var(--text-secondary)] inline-flex items-center gap-1">
                  {PART_LABEL[k] ?? k}
                  {isTerm && <InfoTip term={k as TermKey} />}
                </span>
                <span className="numeric font-medium text-[var(--text-primary)]">{v.toFixed(2)}</span>
              </div>
              <input
                type="range"
                min={0} max={1} step={0.01}
                value={v}
                onChange={(e) => setWeights({ ...weights, [k]: Number(e.target.value) })}
                className="w-full accent-[var(--brand-500)]"
              />
            </label>
          );
        })}
      </div>
      {hidden > 0 && (
        <p className="text-[11px] text-[var(--text-tertiary)]">已隱藏 {hidden} 個次要指標（沿用目前值）。切到「進階」可顯示。</p>
      )}
      {dirty && (
        <button
          onClick={() => setWeights({ ...defaults })}
          className="self-start text-xs text-[var(--brand-600)] hover:underline inline-flex items-center gap-1"
        >
          <Icon name="restart_alt" size={14} />重設本區塊
        </button>
      )}
    </div>
  );
}

function PresetBar({
  presets, activeName, onApply, onDelete, busy,
}: {
  presets: PresetListResponse;
  activeName: string;
  onApply: (w: WeightSet, name: string) => void;
  onDelete: (name: string) => void;
  busy: boolean;
}) {
  return (
    <section className="rounded-xl border border-[var(--border-default)] bg-surface p-4 flex flex-col gap-3">
      <div className="flex items-baseline gap-2">
        <h2 className="text-sm font-semibold inline-flex items-center gap-1.5">
          <Icon name="palette" size={18} className="text-[var(--brand-500)]" />
          主題式預設
          <InfoTip text="一鍵套用一組調好的權重風格。看不到「保守」「成長」哪個適合你？先點來試試，分數對比表會即時更新。" />
        </h2>
        <span className="text-[11px] text-[var(--text-tertiary)]">點選即套用</span>
      </div>
      <div className="flex flex-wrap gap-2">
        {presets.builtin.map((p: BuiltinPreset) => (
          <PresetChip
            key={p.name}
            label={p.label}
            description={p.description}
            active={activeName === p.name}
            disabled={busy}
            onClick={() => onApply(p.weights, p.name)}
          />
        ))}
      </div>

      {presets.user.length > 0 && (
        <>
          <div className="flex items-baseline gap-2 mt-1">
            <h3 className="text-xs font-semibold text-[var(--text-secondary)] inline-flex items-center gap-1">
              <Icon name="bookmark" size={14} />我的 Preset
            </h3>
            <span className="text-[11px] text-[var(--text-tertiary)]">{presets.user.length} 組</span>
          </div>
          <div className="flex flex-wrap gap-2">
            {presets.user.map((p: UserPreset) => (
              <div key={p.name} className="inline-flex items-stretch rounded-md border border-[var(--border-default)] overflow-hidden">
                <PresetChip
                  label={p.name}
                  description={p.description || "（無描述）"}
                  active={activeName === p.name}
                  disabled={busy}
                  onClick={() => onApply(p.weights, p.name)}
                  embedded
                />
                <button
                  onClick={() => onDelete(p.name)}
                  disabled={busy}
                  className="px-2 text-xs text-[var(--text-tertiary)] hover:text-[var(--color-down)] hover:bg-subtle border-l border-[var(--border-default)] disabled:opacity-50"
                  title={`刪除「${p.name}」`}
                >
                  <Icon name="close" size={14} />
                </button>
              </div>
            ))}
          </div>
        </>
      )}

      {activeName === "custom" && (
        <p className="text-[11px] text-[var(--text-tertiary)]">
          <Icon name="edit" size={12} className="inline align-middle mr-0.5" />
          目前是自訂權重（已偏離選定的 preset）。可按下方「儲存為我的 Preset」保存。
        </p>
      )}
    </section>
  );
}

function PresetChip({
  label, description, active, disabled, onClick, embedded = false,
}: {
  label: string;
  description: string;
  active: boolean;
  disabled: boolean;
  onClick: () => void;
  embedded?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      title={description}
      className={cn(
        "px-3 h-9 text-xs inline-flex items-center gap-1 transition-colors disabled:opacity-50",
        embedded
          ? "border-0 bg-transparent"
          : "rounded-md border border-[var(--border-default)] bg-surface",
        active
          ? "bg-[var(--brand-500)]/15 text-[var(--brand-600)] border-[var(--brand-500)] font-semibold"
          : "text-[var(--text-secondary)] hover:bg-subtle",
      )}
    >
      {active && <Icon name="check" size={14} />}
      {label}
    </button>
  );
}

function ModeToggle({ mode, setMode }: { mode: Mode; setMode: (m: Mode) => void }) {
  return (
    <div className="inline-flex rounded-md border border-[var(--border-default)] overflow-hidden text-xs">
      {(["beginner", "advanced"] as const).map((m) => (
        <button
          key={m}
          onClick={() => setMode(m)}
          className={cn(
            "px-3 h-7 inline-flex items-center gap-1 transition-colors",
            mode === m
              ? "bg-[var(--brand-500)] text-white"
              : "bg-surface text-[var(--text-secondary)] hover:bg-subtle",
          )}
        >
          <Icon name={m === "beginner" ? "school" : "tune"} size={13} />
          {m === "beginner" ? "新手" : "進階"}
        </button>
      ))}
    </div>
  );
}

function TdDim({ oldV, newV }: { oldV: number | null; newV: number | null }) {
  const delta = oldV != null && newV != null ? newV - oldV : null;
  return (
    <Td align="right" numeric>
      <div className="flex flex-col items-end text-xs leading-tight">
        <span className="text-[var(--text-tertiary)]">{oldV == null ? "—" : oldV.toFixed(1)}</span>
        <span className="font-semibold text-[var(--text-primary)]">{newV == null ? "—" : newV.toFixed(1)}</span>
        {delta != null && (
          <span className={cn("text-[10px]", tc(delta))}>
            {delta > 0 ? "+" : ""}{delta.toFixed(1)}
          </span>
        )}
      </div>
    </Td>
  );
}

const tc = (v: number | null | undefined) => toneClass(v, { neutralFg: "secondary" });

const btnSecondary = "inline-flex items-center gap-1.5 px-3 h-9 rounded-md border border-[var(--border-default)] bg-surface text-sm text-[var(--text-secondary)] hover:bg-subtle disabled:opacity-50";
const btnPrimary = "inline-flex items-center gap-1.5 px-3 h-9 rounded-md bg-[var(--brand-500)] text-white text-sm hover:bg-[var(--brand-600)] disabled:opacity-50";

// Th + Td 已從 primitives/Table 來。ThGroup 是 weight-tuner 的特殊三欄群組標頭（中央對齊 + 縱向邊框），保留 local
function ThGroup({ children }: { children: React.ReactNode }) {
  return <th className="h-10 px-4 font-medium text-center border-x border-[var(--border-default)]" colSpan={1}>{children}</th>;
}
