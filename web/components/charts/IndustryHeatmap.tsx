"use client";

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { hierarchy, treemap, treemapSquarify } from "d3-hierarchy";
import type { HierarchyRectangularNode } from "d3-hierarchy";
import type { IndustryRotationRow } from "@/lib/api";
import { fmtPct, fmtTradeValue } from "@/lib/format";

// 固定 ±10% 色階 — 每 2% 一階，共 11 階。
// 用 color-mix 把 --color-up / --color-down 跟主題的 surface 混合，
// 同一份程式碼在 light/dark 主題下都會自動對齊（避免 100~900 token 沒 dark override 的問題）。
type Bucket = { bg: string; fg: string };

const mix = (color: string, pct: number) =>
  `color-mix(in srgb, var(${color}) ${pct}%, var(--bg-surface))`;

const NEUTRAL: Bucket = { bg: "var(--bg-subtle)", fg: "var(--text-secondary)" };

// 上一版兩主題太接近 → 拉開首兩階比例（淺階更淺、中階更飽和）
const UP_BUCKETS: Bucket[] = [
  { bg: mix("--color-up", 18), fg: "var(--text-primary)" },   // +1% ~ +3%
  { bg: mix("--color-up", 38), fg: "var(--text-primary)" },   // +3% ~ +5%
  { bg: mix("--color-up", 62), fg: "#FFFFFF" },               // +5% ~ +7%
  { bg: mix("--color-up", 84), fg: "#FFFFFF" },               // +7% ~ +9%
  { bg: "var(--color-up)",     fg: "#FFFFFF" },               // > +9%
];
const DOWN_BUCKETS: Bucket[] = [
  { bg: mix("--color-down", 18), fg: "var(--text-primary)" },
  { bg: mix("--color-down", 38), fg: "var(--text-primary)" },
  { bg: mix("--color-down", 62), fg: "#FFFFFF" },
  { bg: mix("--color-down", 84), fg: "#FFFFFF" },
  { bg: "var(--color-down)",     fg: "#FFFFFF" },
];

function bucketFor(ret: number | null): Bucket {
  if (ret == null || !Number.isFinite(ret)) return NEUTRAL;
  const pct = ret * 100;
  if (pct > -1 && pct < 1) return NEUTRAL;
  const abs = Math.abs(pct);
  let idx: number;
  if (abs >= 9) idx = 4;
  else if (abs >= 7) idx = 3;
  else if (abs >= 5) idx = 2;
  else if (abs >= 3) idx = 1;
  else idx = 0;
  // idx 永遠在 [0, 4]、bucket array 長度也是 5，但 noUncheckedIndexedAccess 不知道。
  // 用 ?? NEUTRAL 給型別保險（runtime 不可能走到 fallback）。
  return (pct > 0 ? UP_BUCKETS[idx] : DOWN_BUCKETS[idx]) ?? NEUTRAL;
}

type Datum = {
  industry: string;
  size: number;             // dataKey for area = totalAmount
  nMembers: number;
  totalAmount: number;
  pct: number;              // totalAmount / sum(totalAmount)
  ret: number | null;       // ret_1d_weighted
  nUp: number;
  nFlat: number;
  nDown: number;
  bg: string;
  fg: string;
};

type LaidTile = Datum & { x: number; y: number; w: number; h: number };

// 高度動態：依容器寬度推 0.55 比例，再 clamp 在 [380, 560]
// 原本固定 560 在筆電/平板上會過高（佔超過一個 viewport），縮小裝置看治理為 380。
const PADDING = 2;
function computeHeight(width: number): number {
  if (width <= 0) return 480;
  return Math.round(Math.max(380, Math.min(560, width * 0.55)));
}

// 中文字寬 ≈ fontSize，英數 ≈ fontSize * 0.55
function estimateTextWidth(text: string, fontSize: number): number {
  let w = 0;
  for (const ch of text) {
    w += /[一-鿿　-〿＀-￯]/.test(ch) ? fontSize : fontSize * 0.55;
  }
  return w;
}

function truncateToWidth(text: string, fontSize: number, maxWidth: number): string {
  if (estimateTextWidth(text, fontSize) <= maxWidth) return text;
  const ellipsisW = fontSize * 0.55 * 1.5; // "…" 視為英數半形寬度的 1.5 倍保險
  let acc = 0;
  let out = "";
  for (const ch of text) {
    const w = /[一-鿿　-〿＀-￯]/.test(ch) ? fontSize : fontSize * 0.55;
    if (acc + w + ellipsisW > maxWidth) break;
    out += ch;
    acc += w;
  }
  return out + "…";
}

export function IndustryHeatmap({
  data,
  asOf,
}: {
  data: IndustryRotationRow[];
  asOf: string | null;
}) {
  const router = useRouter();
  const containerRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  const [hover, setHover] = useState<LaidTile | null>(null);
  const height = computeHeight(width);

  // 量容器寬度（高度由 width 推導）
  // ResizeObserver 在拖拉視窗時會逐 px 觸發，d3-hierarchy 是 O(n log n) 的 squarify
  // → 用 rAF 合併同一 frame 內的多次 resize，避免 60fps drag 跑 60 次 layout。
  useLayoutEffect(() => {
    if (!containerRef.current) return;
    const el = containerRef.current;
    let rafId: number | null = null;
    const apply = () => {
      rafId = null;
      setWidth(el.clientWidth);
    };
    const schedule = () => {
      if (rafId != null) return;
      rafId = requestAnimationFrame(apply);
    };
    // 第一次同步量，避免首 frame 沒寬度
    setWidth(el.clientWidth);
    const ro = new ResizeObserver(schedule);
    ro.observe(el);
    return () => {
      ro.disconnect();
      if (rafId != null) cancelAnimationFrame(rafId);
    };
  }, []);

  // 1. data 預處理（只算一次）
  const data0 = useMemo<Datum[]>(() => {
    const totalSum = data.reduce(
      (acc, d) => acc + (d.totalAmount && d.totalAmount > 0 ? d.totalAmount : 0),
      0,
    );
    return data.map((d) => {
      const amt = d.totalAmount && d.totalAmount > 0 ? d.totalAmount : 0;
      const size = amt > 0 ? amt : 1;
      const ret = d.ret1DWeighted;
      const { bg, fg } = bucketFor(ret);
      return {
        industry: d.industry,
        size,
        nMembers: d.nMembers,
        totalAmount: amt,
        pct: totalSum > 0 ? amt / totalSum : 0,
        ret,
        nUp: d.nUp,
        nFlat: d.nFlat,
        nDown: d.nDown,
        bg,
        fg,
      };
    });
  }, [data]);

  // 2. d3-hierarchy 算 layout
  const tiles = useMemo<LaidTile[]>(() => {
    if (width <= 0 || data0.length === 0) return [];
    // d3-hierarchy 的 root 節點型別需含 `children?` 與所有 leaf 欄位（leaf 也是 root 同型）
    type Node = { children?: Datum[] } & Partial<Datum>;
    const root = hierarchy<Node>({ children: data0 })
      .sum((d) => (d.size ?? 0))
      .sort((a, b) => (b.value ?? 0) - (a.value ?? 0)); // ★ 關鍵：sort 是 squarify 不退化的前提
    const layout = treemap<Node>()
      .tile(treemapSquarify.ratio(1.6))
      .size([width, height])
      .paddingInner(PADDING)
      .round(true);
    layout(root);
    // layout() 之後 root 的子節點都是 HierarchyRectangularNode（含 x0/y0/x1/y1）
    const leaves = root.leaves() as HierarchyRectangularNode<Node>[];
    return leaves.map((leaf) => {
      // leaf 是用 data0 的 Datum 構造的，data 一定是完整 Datum（不只是 Partial）
      const d = leaf.data as Datum;
      return {
        ...d,
        x: leaf.x0,
        y: leaf.y0,
        w: Math.max(0, leaf.x1 - leaf.x0),
        h: Math.max(0, leaf.y1 - leaf.y0),
      };
    });
  }, [width, height, data0]);

  const onTileClick = (industry: string) => {
    router.push(`/sectors?industry=${encodeURIComponent(industry)}`);
  };

  return (
    <div className="flex flex-col gap-3">
      <div className="rounded-xl border border-[var(--border-default)] bg-surface p-2">
        <div
          ref={containerRef}
          className="relative"
          style={{ height }}
        >
          {width > 0 && (
            <svg
              width={width}
              height={height}
              role="img"
              aria-label="產業熱力圖"
              style={{ display: "block" }}
            >
              {tiles.map((t) => (
                <Tile
                  key={t.industry}
                  tile={t}
                  onClick={() => onTileClick(t.industry)}
                  onEnter={() => setHover(t)}
                  onLeave={() => setHover((h) => (h?.industry === t.industry ? null : h))}
                />
              ))}
            </svg>
          )}
          {hover && containerRef.current && (
            <HoverCard tile={hover} containerEl={containerRef.current} />
          )}
        </div>
      </div>

      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-3 flex-wrap">
          <ColorScaleLegend />
          {asOf && (
            <span className="text-[10px] text-[var(--text-tertiary)] numeric">
              資料截至 {asOf}
            </span>
          )}
        </div>
        <p className="text-xs text-[var(--text-tertiary)] leading-relaxed max-w-2xl">
          磚塊面積 = 該產業最新交易日成交值占大盤比；顏色 = 成交值加權當日漲跌（紅漲綠跌、固定 ±10% 色階）。點任一磚進該產業成員股頁。
        </p>
      </div>
    </div>
  );
}

function Tile({
  tile,
  onClick,
  onEnter,
  onLeave,
}: {
  tile: LaidTile;
  onClick: () => void;
  onEnter: () => void;
  onLeave: () => void;
}) {
  const { x, y, w, h, industry, ret, bg, fg } = tile;
  if (w < 1 || h < 1) return null;

  // 動態字級：磚面積開根號縮放，clamp 到 [11, 36]
  const area = w * h;
  const baseFont = Math.max(11, Math.min(36, Math.sqrt(area) / 8));
  const pctFont = Math.max(10, Math.min(28, baseFont * 0.7));

  const innerPad = 6;
  const usableW = w - innerPad * 2;
  const showName = w >= 36 && h >= 22;
  const showPct = w >= 60 && h >= baseFont + pctFont + 8;

  const nameText = showName ? truncateToWidth(industry, baseFont, usableW) : "";
  const pctText = showPct ? (ret == null ? "—" : fmtPct(ret, 2)) : "";

  return (
    <g
      style={{ cursor: "pointer" }}
      onClick={onClick}
      onMouseEnter={onEnter}
      onMouseLeave={onLeave}
    >
      {/* 用 style.fill 而非 fill 屬性，這樣 color-mix() 之類的 CSS 函式才會被解析 */}
      <rect
        x={x}
        y={y}
        width={w}
        height={h}
        stroke="var(--bg-surface)"
        strokeWidth={2}
        rx={2}
        style={{ fill: bg }}
      />
      {showName && nameText && (
        <text
          x={x + w / 2}
          y={y + h / 2 - (showPct ? pctFont * 0.6 : 0)}
          fontSize={baseFont}
          fontWeight={600}
          textAnchor="middle"
          dominantBaseline="central"
          style={{ fill: fg, pointerEvents: "none" }}
        >
          {nameText}
        </text>
      )}
      {showPct && (
        <text
          x={x + w / 2}
          y={y + h / 2 + baseFont * 0.7}
          fontSize={pctFont}
          textAnchor="middle"
          dominantBaseline="central"
          style={{
            fill: fg,
            fontVariantNumeric: "tabular-nums",
            pointerEvents: "none",
          }}
        >
          {pctText}
        </text>
      )}
    </g>
  );
}

function HoverCard({ tile, containerEl }: { tile: LaidTile; containerEl: HTMLElement }) {
  // 錨在磚的右外側；超出 viewport 翻左外側；上下 clamp 不超出畫面
  const cardW = 220;
  const cardH = 160;
  const gap = 8;

  const [pos, setPos] = useState<{ left: number; top: number } | null>(null);

  useEffect(() => {
    const rect = containerEl.getBoundingClientRect();
    const tileRight = rect.left + tile.x + tile.w;
    const tileLeft = rect.left + tile.x;
    const tileTop = rect.top + tile.y;
    const winW = window.innerWidth;
    const winH = window.innerHeight;
    let left = tileRight + gap;
    if (left + cardW > winW - 8) {
      left = tileLeft - gap - cardW;
    }
    if (left < 8) left = 8;
    let top = tileTop;
    if (top + cardH > winH - 8) top = winH - 8 - cardH;
    if (top < 8) top = 8;
    setPos({ left, top });
  }, [tile, containerEl]);

  const tone =
    tile.ret == null
      ? "var(--text-secondary)"
      : tile.ret > 0
      ? "var(--color-up)"
      : tile.ret < 0
      ? "var(--color-down)"
      : "var(--text-secondary)";

  if (!pos) return null;

  return (
    <div
      className="fixed z-50 rounded-lg border border-[var(--border-default)] bg-surface shadow-lg p-3 text-xs pointer-events-none"
      style={{ left: pos.left, top: pos.top, width: cardW }}
    >
      <div className="font-semibold text-[var(--text-primary)] text-sm mb-2">
        {tile.industry}
      </div>
      <div className="flex items-baseline justify-between mb-2">
        <span className="text-[var(--text-tertiary)]">當日加權報酬</span>
        <span
          className="numeric text-base font-bold"
          style={{ color: tone, fontVariantNumeric: "tabular-nums" }}
        >
          {tile.ret == null ? "—" : fmtPct(tile.ret, 2)}
        </span>
      </div>
      <div className="grid grid-cols-3 gap-1 mb-2 text-center">
        <Stat label="上漲" value={tile.nUp} color="var(--color-up)" />
        <Stat label="持平" value={tile.nFlat} color="var(--text-secondary)" />
        <Stat label="下跌" value={tile.nDown} color="var(--color-down)" />
      </div>
      <div className="flex justify-between text-[10px] text-[var(--text-tertiary)] pt-2 border-t border-[var(--border-default)]">
        <span>成交值 {fmtTradeValue(tile.totalAmount)}</span>
        <span className="numeric">大盤占比 {(tile.pct * 100).toFixed(1)}%</span>
      </div>
    </div>
  );
}

function Stat({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div className="rounded bg-subtle py-1">
      <div className="text-[10px] text-[var(--text-tertiary)] leading-none">{label}</div>
      <div
        className="numeric text-sm font-semibold leading-tight mt-0.5"
        style={{ color, fontVariantNumeric: "tabular-nums" }}
      >
        {value}
      </div>
    </div>
  );
}

function ColorScaleLegend() {
  // 連續色帶：left 深綠 → 中性灰 → right 深紅；兩端標 ±10%
  // 跟 bucketFor 同一份混色公式，主題切換會自動跟著
  const stops = [
    "var(--color-down)",
    mix("--color-down", 84),
    mix("--color-down", 62),
    mix("--color-down", 38),
    mix("--color-down", 18),
    "var(--bg-subtle)",
    mix("--color-up", 18),
    mix("--color-up", 38),
    mix("--color-up", 62),
    mix("--color-up", 84),
    "var(--color-up)",
  ];
  return (
    <div className="flex items-center gap-2 text-[10px] text-[var(--text-tertiary)]">
      <span className="numeric">−10%</span>
      <div className="flex h-3 w-48 rounded overflow-hidden border border-[var(--border-default)]">
        {stops.map((c, i) => (
          <div key={i} className="flex-1" style={{ background: c }} />
        ))}
      </div>
      <span className="numeric">+10%</span>
    </div>
  );
}
