"use client";

/**
 * 個股評分面板（含「收盤 / 即時 / 假設」三模式切換）。
 *
 * 為什麼要拆 client component：
 * - score_stock 是即時 compute（非讀 snapshot），所以盤中價變動 → 短/中分數會跟著動
 * - 但每天 16:30 才能更新 daily_price → 隔天進場時看到的「昨日收盤分數」對短打交易已經過時
 * - 此面板讓使用者：(a) 切到「即時」拿 mis 盤中價重算、(b) 切到「假設」測試「如果 X 價買進」
 * - 結構分（中/長期 ROE/EPS/股利）不會被盤中價影響；只有短期會明顯跳動
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  apiGet,
  type IntradayQuoteView,
  type StockScoreView,
} from "@/lib/api";
import { Icon } from "@/components/primitives/Icon";
import { ScoreBadge } from "@/components/primitives/ScoreBadge";
import { ScoreBreakdownBars } from "@/components/primitives/ScoreBreakdownBars";
import { RecommendationTag } from "@/components/primitives/RecommendationTag";
import { SectionTitle } from "@/components/primitives/SectionTitle";
import { fmtPct, fmtPrice, fmtScore, toneClass } from "@/lib/format";
import { cn } from "@/lib/utils";

type Mode = "close" | "live" | "whatif";

const LIVE_REFRESH_MS = 30_000;
const WHATIF_DEBOUNCE_MS = 350;

export function StockScorePanel({
  stockId,
  initialScore,
}: {
  stockId: string;
  initialScore: StockScoreView;
}) {
  const baseClose = initialScore.close;
  const [mode, setMode] = useState<Mode>("close");
  const [score, setScore] = useState<StockScoreView>(initialScore);
  const [intraday, setIntraday] = useState<IntradayQuoteView | null>(null);
  const [whatIfPrice, setWhatIfPrice] = useState<number>(baseClose);
  const [whatIfText, setWhatIfText] = useState<string>(fmtPrice(baseClose).replace(/,/g, ""));
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // 用 ref 抓最新的 abort，切模式時取消 in-flight 的請求避免閃回舊值
  const abortRef = useRef<AbortController | null>(null);

  const cancelInFlight = () => {
    abortRef.current?.abort();
    abortRef.current = null;
  };

  const fetchScore = useCallback(
    async (params: string, signal: AbortSignal): Promise<StockScoreView | null> => {
      try {
        return await apiGet<StockScoreView>(
          `/api/stocks/${encodeURIComponent(stockId)}/score${params ? `?${params}` : ""}`,
          { noCache: true },
        );
      } catch (e) {
        if (signal.aborted) return null;
        setError(e instanceof Error ? e.message : String(e));
        return null;
      }
    },
    [stockId],
  );

  // 即時 mode：先抓 intraday → 再用 ?live=1 算一次 score；之後每 30 秒重抓
  useEffect(() => {
    if (mode !== "live") return;
    let cancelled = false;
    cancelInFlight();
    const ctrl = new AbortController();
    abortRef.current = ctrl;

    const tick = async () => {
      setLoading(true);
      setError(null);
      try {
        const q = await apiGet<IntradayQuoteView>(
          `/api/stocks/${encodeURIComponent(stockId)}/intraday`,
          { noCache: true },
        );
        if (cancelled || ctrl.signal.aborted) return;
        setIntraday(q);
        const next = await fetchScore("live=1", ctrl.signal);
        if (cancelled || ctrl.signal.aborted) return;
        if (next) setScore(next);
      } catch (e) {
        if (cancelled || ctrl.signal.aborted) return;
        // intraday 失敗（興櫃 / 休市 / mis 異常）→ 顯示警告，分數退回收盤
        setIntraday(null);
        setScore(initialScore);
        setError(e instanceof Error ? e.message : "即時報價無法取得");
      } finally {
        if (!cancelled && !ctrl.signal.aborted) setLoading(false);
      }
    };

    tick();
    const handle = window.setInterval(tick, LIVE_REFRESH_MS);
    return () => {
      cancelled = true;
      window.clearInterval(handle);
      ctrl.abort();
    };
  }, [mode, stockId, fetchScore, initialScore]);

  // 假設 mode：debounce 350ms 後 fetch
  useEffect(() => {
    if (mode !== "whatif") return;
    if (!Number.isFinite(whatIfPrice) || whatIfPrice <= 0) return;
    cancelInFlight();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    const t = window.setTimeout(async () => {
      setLoading(true);
      setError(null);
      const next = await fetchScore(
        `override_price=${encodeURIComponent(whatIfPrice.toString())}`,
        ctrl.signal,
      );
      if (!ctrl.signal.aborted) {
        if (next) setScore(next);
        setLoading(false);
      }
    }, WHATIF_DEBOUNCE_MS);
    return () => {
      window.clearTimeout(t);
      ctrl.abort();
    };
  }, [mode, whatIfPrice, fetchScore]);

  // 收盤 mode：恢復 initialScore
  useEffect(() => {
    if (mode !== "close") return;
    cancelInFlight();
    setScore(initialScore);
    setError(null);
    setLoading(false);
  }, [mode, initialScore]);

  // What-if 滑桿範圍：基準收盤價 ±10%（漲跌停）。step = 0.05（最小報價單位 ~ NT$0.05~5，這裡折衷）
  const whatIfMin = useMemo(() => +(baseClose * 0.9).toFixed(2), [baseClose]);
  const whatIfMax = useMemo(() => +(baseClose * 1.1).toFixed(2), [baseClose]);
  const whatIfStep = useMemo(() => {
    if (baseClose >= 1000) return 1;
    if (baseClose >= 100) return 0.5;
    if (baseClose >= 10) return 0.05;
    return 0.01;
  }, [baseClose]);

  const onWhatIfTextBlur = () => {
    const v = parseFloat(whatIfText);
    if (Number.isFinite(v) && v > 0) {
      const clamped = Math.max(whatIfMin, Math.min(whatIfMax, v));
      setWhatIfPrice(clamped);
      setWhatIfText(clamped.toString());
    } else {
      setWhatIfText(whatIfPrice.toString());
    }
  };

  return (
    <>
      <ModeBar
        mode={mode}
        onChange={(m) => setMode(m)}
        baseClose={baseClose}
        intraday={intraday}
        whatIfPrice={whatIfPrice}
        whatIfText={whatIfText}
        whatIfMin={whatIfMin}
        whatIfMax={whatIfMax}
        whatIfStep={whatIfStep}
        onWhatIfChange={(v) => {
          setWhatIfPrice(v);
          setWhatIfText(v.toString());
        }}
        onWhatIfText={(t) => setWhatIfText(t)}
        onWhatIfTextBlur={onWhatIfTextBlur}
        onWhatIfReset={() => {
          setWhatIfPrice(baseClose);
          setWhatIfText(baseClose.toString());
        }}
        loading={loading}
        error={error}
      />

      {/* 5-KPI row */}
      <section className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <ScoreCard
          label="短期"
          score={score.short.total}
          horizon="short"
          completeness={score.short.completeness}
          delta={mode === "close" ? null : (score.short.total ?? 0) - (initialScore.short.total ?? 0)}
        />
        <ScoreCard
          label="中期"
          score={score.mid.total}
          horizon="mid"
          completeness={score.mid.completeness}
          delta={mode === "close" ? null : (score.mid.total ?? 0) - (initialScore.mid.total ?? 0)}
        />
        <ScoreCard
          label="長期"
          score={score.long.total}
          horizon="long"
          completeness={score.long.completeness}
          structuralOnly
        />
        <ScoreCard
          label="綜合"
          score={score.compositeScore}
          horizon="composite"
          completeness={score.dataCompleteness}
          delta={mode === "close" ? null : (score.compositeScore ?? 0) - (initialScore.compositeScore ?? 0)}
        />
        <div className="rounded-xl border border-[var(--border-default)] bg-surface p-4 flex flex-col gap-2">
          <span className="text-xs text-[var(--text-tertiary)] flex items-center gap-1.5">建議</span>
          <div className="flex items-center h-8">
            <RecommendationTag raw={score.recommendation} />
          </div>
          <span className="text-xs text-[var(--text-tertiary)] mt-auto">
            資料日期 {score.asOf}
            {score.isStale && (
              <span className="ml-1.5 inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[11px] bg-[var(--warning-bg)] text-[var(--warning-fg)] border border-[var(--warning-border)]">
                <Icon name="warning" size={11} filled />
                過期 {score.staleDays} 天
              </span>
            )}
            {score.isPending && (
              <span
                className="ml-1.5 inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[11px] bg-[var(--warning-bg)] text-[var(--warning-fg)] border border-[var(--warning-border)]"
                title="盤中暫態：14:00 後才視為當日確定收盤"
              >
                <Icon name="hourglass_empty" size={11} filled />
                盤中暫態
              </span>
            )}
          </span>
        </div>
      </section>

      {/* Breakdown + signals */}
      <section className="grid grid-cols-1 xl:grid-cols-[1fr_1.1fr] gap-6">
        <div className="flex flex-col gap-4">
          <SectionTitle icon="analytics">評分拆解</SectionTitle>
          <div className="rounded-xl border border-[var(--border-default)] bg-surface p-5 flex flex-col gap-5">
            <BreakdownBlock title="短期" total={score.short.total} parts={score.short.parts} />
            <BreakdownBlock title="中期" total={score.mid.total} parts={score.mid.parts} />
            <BreakdownBlock title="長期" total={score.long.total} parts={score.long.parts} />
          </div>
        </div>
        <div className="flex flex-col gap-4">
          <SectionTitle icon="lightbulb">進出場建議</SectionTitle>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <SignalCard title="進場訊號" items={score.entry} tone="up" icon="login" />
            <SignalCard title="停損參考" items={score.stopLoss} tone="neutral" icon="shield" />
            <SignalCard title="停利參考" items={score.takeProfit} tone="down" icon="flag" />
            <SignalCard
              title="風險提示"
              items={score.warnings.length ? score.warnings : ["無明顯風險訊號"]}
              tone="warning"
              icon="warning"
            />
          </div>
        </div>
      </section>
    </>
  );
}

// =====================================================================
// Mode toggle bar
// =====================================================================
function ModeBar({
  mode,
  onChange,
  baseClose,
  intraday,
  whatIfPrice,
  whatIfText,
  whatIfMin,
  whatIfMax,
  whatIfStep,
  onWhatIfChange,
  onWhatIfText,
  onWhatIfTextBlur,
  onWhatIfReset,
  loading,
  error,
}: {
  mode: Mode;
  onChange: (m: Mode) => void;
  baseClose: number;
  intraday: IntradayQuoteView | null;
  whatIfPrice: number;
  whatIfText: string;
  whatIfMin: number;
  whatIfMax: number;
  whatIfStep: number;
  onWhatIfChange: (v: number) => void;
  onWhatIfText: (t: string) => void;
  onWhatIfTextBlur: () => void;
  onWhatIfReset: () => void;
  loading: boolean;
  error: string | null;
}) {
  return (
    <section className="rounded-xl border border-[var(--border-default)] bg-surface p-4 flex flex-col gap-3">
      <div className="flex items-center gap-3 flex-wrap">
        <div className="inline-flex rounded-lg border border-[var(--border-default)] overflow-hidden text-sm">
          <ModeButton active={mode === "close"} onClick={() => onChange("close")} icon="check_circle">
            收盤
          </ModeButton>
          <ModeButton active={mode === "live"} onClick={() => onChange("live")} icon="bolt">
            即時
          </ModeButton>
          <ModeButton active={mode === "whatif"} onClick={() => onChange("whatif")} icon="tune">
            假設
          </ModeButton>
        </div>
        {loading && (
          <span className="inline-flex items-center gap-1 text-xs text-[var(--text-tertiary)]">
            <Icon name="progress_activity" size={14} className="animate-spin" />
            重算中
          </span>
        )}
        {error && (
          <span className="inline-flex items-center gap-1 text-xs text-[var(--warning-fg)]" title={error}>
            <Icon name="warning" size={14} filled />
            {error.length > 40 ? "資料異常" : error}
          </span>
        )}
        {mode === "close" && (
          <span className="text-xs text-[var(--text-tertiary)]">
            收盤 NT$ <span className="numeric font-medium">{fmtPrice(baseClose)}</span> · 短/中分數依昨日收盤計算
          </span>
        )}
        {mode === "live" && intraday && (
          <span className="inline-flex items-center gap-2 text-xs">
            <span className="text-[var(--text-tertiary)]">
              即時 NT$ <span className="numeric font-medium text-[var(--text-primary)]">{fmtPrice(intraday.price)}</span>
            </span>
            {intraday.changePct != null && (
              <span className={cn("numeric font-medium", toneClass(intraday.changePct))}>
                {fmtPct(intraday.changePct, 2)}
              </span>
            )}
            {intraday.quoteTime && (
              <span className="text-[var(--text-tertiary)]">@ {intraday.quoteTime}</span>
            )}
            <QuoteSourceTag source={intraday.quoteSource} bid={intraday.bid1} ask={intraday.ask1} />
          </span>
        )}
        {mode === "live" && !intraday && !loading && (
          <span className="text-xs text-[var(--text-tertiary)]">即時報價無法取得（興櫃 / 休市 / 上游異常）</span>
        )}
      </div>

      {mode === "whatif" && (
        <div className="flex flex-col gap-2 mt-1">
          <div className="flex items-center gap-3 flex-wrap">
            <label className="text-xs text-[var(--text-tertiary)] flex items-center gap-1.5">
              <Icon name="tune" size={14} />
              假設成交價
            </label>
            <input
              type="text"
              inputMode="decimal"
              value={whatIfText}
              onChange={(e) => onWhatIfText(e.target.value)}
              onBlur={onWhatIfTextBlur}
              onKeyDown={(e) => {
                if (e.key === "Enter") (e.target as HTMLInputElement).blur();
              }}
              className="numeric w-28 px-2 py-1 text-sm rounded border border-[var(--border-default)] bg-surface focus:outline-none focus:ring-2 focus:ring-[var(--brand-500)]/40"
            />
            <span className={cn("text-xs numeric font-medium", toneClass((whatIfPrice - baseClose) / baseClose))}>
              {fmtPct((whatIfPrice - baseClose) / baseClose, 2)}
            </span>
            <span className="text-xs text-[var(--text-tertiary)]">vs 收盤 NT$ {fmtPrice(baseClose)}</span>
            <button
              type="button"
              onClick={onWhatIfReset}
              className="text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)] underline-offset-2 hover:underline"
            >
              重設
            </button>
          </div>
          <div className="flex items-center gap-3">
            <span className="numeric text-[11px] text-[var(--text-tertiary)] w-14 text-right">−10% {fmtPrice(whatIfMin)}</span>
            <input
              type="range"
              min={whatIfMin}
              max={whatIfMax}
              step={whatIfStep}
              value={whatIfPrice}
              onChange={(e) => onWhatIfChange(parseFloat(e.target.value))}
              className="flex-1 accent-[var(--brand-500)]"
              aria-label="假設成交價"
            />
            <span className="numeric text-[11px] text-[var(--text-tertiary)] w-14">{fmtPrice(whatIfMax)} +10%</span>
          </div>
        </div>
      )}
    </section>
  );
}

function QuoteSourceTag({
  source,
  bid,
  ask,
}: {
  source: IntradayQuoteView["quoteSource"];
  bid: number | null;
  ask: number | null;
}) {
  // match 是「最新撮合」— 不需特別標，看起來最自然，跳過避免雜訊
  if (source === "match") return null;
  type FallbackSource = Exclude<IntradayQuoteView["quoteSource"], "match">;
  const config: Record<FallbackSource, { label: string; tooltip: string; tone: "info" | "warning" | "up" | "down" }> = {
    prev_match: {
      label: "前筆撮合",
      tooltip: "TWSE 5 秒撮合間，最新撮合價尚未產生；以上一筆撮合價當作即時參考。",
      tone: "info",
    },
    limit_up: {
      label: "漲停鎖死",
      tooltip: "今日高點觸及漲停價且仍有成交；mis 在鎖死期 z/pz 常空白、ask 全空，直接以漲停價當當下價。",
      tone: "up",
    },
    limit_down: {
      label: "跌停鎖死",
      tooltip: "今日低點觸及跌停價且仍有成交；mis 在鎖死期 z/pz 常空白、bid 全空，直接以跌停價當當下價。",
      tone: "down",
    },
    midpoint: {
      label: bid != null && ask != null ? `中價 ${fmtPrice(bid)}/${fmtPrice(ask)}` : "買賣中價",
      tooltip:
        "TWSE 5 秒撮合間，最新／前筆撮合價皆空白；以最佳買賣 5 檔的中價 (b1+a1)/2 作為當下可成交估價。",
      tone: "info",
    },
    ask_only: {
      label: "僅有賣價",
      tooltip: "委買簿空、僅有委賣；以最佳賣價當作當下成交估價（急跌或冷門股偶見）。",
      tone: "info",
    },
    bid_only: {
      label: "僅有買價",
      tooltip: "委賣簿空、僅有委買；以最佳買價當作當下成交估價。",
      tone: "info",
    },
    prev_close: {
      label: "非盤中（昨收）",
      tooltip: "盤前／休市／mis 異常，price 用昨收 fallback；分數退回收盤版本。",
      tone: "warning",
    },
  };
  const c = config[source];
  if (!c.label) return null;
  const cls =
    c.tone === "warning"
      ? "bg-[var(--warning-bg)] text-[var(--warning-fg)] border-[var(--warning-border)]"
      : c.tone === "up"
      ? "bg-[var(--color-up-bg)] text-[var(--color-up)] border-[var(--color-up-border)]"
      : c.tone === "down"
      ? "bg-[var(--color-down-bg)] text-[var(--color-down)] border-[var(--color-down-border)]"
      : "bg-subtle text-[var(--text-tertiary)] border-[var(--border-default)]";
  const iconName =
    c.tone === "warning" ? "schedule" :
    c.tone === "up" ? "north" :
    c.tone === "down" ? "south" :
    "info";
  return (
    <span
      className={cn("inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[11px] border", cls)}
      title={c.tooltip}
    >
      <Icon name={iconName} size={11} />
      {c.label}
    </span>
  );
}

function ModeButton({
  active,
  onClick,
  icon,
  children,
}: {
  active: boolean;
  onClick: () => void;
  icon: string;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "inline-flex items-center gap-1.5 px-3 py-1.5 transition-colors",
        active
          ? "bg-[var(--brand-500)] text-white"
          : "bg-surface text-[var(--text-secondary)] hover:bg-subtle",
      )}
      aria-pressed={active}
    >
      <Icon name={icon} size={14} filled={active} />
      {children}
    </button>
  );
}

// =====================================================================
// Score / breakdown / signal cards (same visual language as before)
// =====================================================================
function ScoreCard({
  label,
  score,
  horizon,
  completeness,
  delta,
  structuralOnly,
}: {
  label: string;
  score: number | null;
  horizon: "short" | "mid" | "long" | "composite";
  completeness?: number;
  delta?: number | null;
  structuralOnly?: boolean;
}) {
  const isLowTrust = completeness != null && completeness < 0.6;
  const showDelta = !structuralOnly && delta != null && Math.abs(delta) >= 0.1;
  return (
    <div className="rounded-xl border border-[var(--border-default)] bg-surface p-4 flex flex-col gap-2">
      <span className="text-xs text-[var(--text-tertiary)] flex items-center gap-1.5">
        {label}分數
        {structuralOnly && (
          <span
            className="inline-flex items-center px-1 rounded text-[11px] bg-subtle text-[var(--text-tertiary)] border border-[var(--border-default)]"
            title="長期分數依 ROE / EPS / 股利等財報指標，不受盤中價變動影響"
          >
            結構
          </span>
        )}
        {isLowTrust && (
          <span
            className="inline-flex items-center px-1 rounded text-[11px] bg-subtle text-[var(--text-tertiary)] border border-[var(--border-default)]"
            title={`僅 ${Math.round((completeness ?? 0) * 100)}% 子指標有資料，分數可信度較低`}
          >
            資料 {Math.round((completeness ?? 0) * 100)}%
          </span>
        )}
      </span>
      <div className="flex items-baseline gap-2">
        <span className="numeric text-[32px] font-bold leading-none text-[var(--text-primary)]">
          {fmtScore(score)}
        </span>
        <ScoreBadge score={score} size="sm" horizon={horizon} />
        {showDelta && (
          <span
            className={cn(
              "text-xs numeric font-medium",
              delta! > 0 ? "text-[var(--color-up)]" : "text-[var(--color-down)]",
            )}
          >
            {delta! > 0 ? "+" : "−"}
            {Math.abs(delta!).toFixed(1)}
          </span>
        )}
      </div>
    </div>
  );
}

function BreakdownBlock({
  title,
  total,
  parts,
}: {
  title: string;
  total: number | null;
  parts: Record<string, number | null>;
}) {
  return (
    <div>
      <div className="flex items-baseline gap-2 mb-3">
        <span className="text-sm font-semibold text-[var(--text-secondary)]">{title}</span>
        <ScoreBadge score={total} size="sm" />
      </div>
      <ScoreBreakdownBars parts={parts} />
    </div>
  );
}

function SignalCard({
  title,
  items,
  tone,
  icon,
}: {
  title: string;
  items: string[];
  tone: "up" | "down" | "neutral" | "warning";
  icon: string;
}) {
  const cls =
    tone === "up"
      ? "text-[var(--color-up)] bg-[var(--color-up-bg)] border-[var(--color-up-border)]"
      : tone === "down"
      ? "text-[var(--color-down)] bg-[var(--color-down-bg)] border-[var(--color-down-border)]"
      : tone === "warning"
      ? "text-[var(--warning-fg)] bg-[var(--warning-bg)] border-[var(--warning-border)]"
      : "text-[var(--text-secondary)] bg-subtle border-[var(--border-default)]";
  return (
    <div className="rounded-xl border border-[var(--border-default)] bg-surface p-4 flex flex-col gap-2.5">
      <div className={`inline-flex items-center gap-1.5 self-start px-2 py-1 rounded border ${cls}`}>
        <Icon name={icon} size={16} filled />
        <span className="text-xs font-semibold">{title}</span>
      </div>
      <ul className="flex flex-col gap-1.5 text-sm text-[var(--text-primary)] pl-1">
        {items.map((x, i) => (
          <li key={i} className="flex gap-2 leading-snug">
            <span className="text-[var(--text-tertiary)] shrink-0">·</span>
            <span>{x}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
