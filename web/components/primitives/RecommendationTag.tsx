import { cn } from "@/lib/utils";

/** 從 scoring/engine.recommendation_label 回傳的字串拆出 emoji + 文字 */
function parseRecommendation(raw: string): { tone: "strong-buy" | "buy" | "hold" | "sell" | "strong-sell"; text: string } {
  const text = raw.replace(/^[^一-龥A-Za-z]*/, "").trim();  // 去頭部 emoji
  if (raw.includes("強力偏多")) return { tone: "strong-buy", text };
  if (raw.includes("偏多"))     return { tone: "buy", text };
  if (raw.includes("中性"))     return { tone: "hold", text };
  if (raw.includes("強力偏空")) return { tone: "strong-sell", text };
  if (raw.includes("偏空"))     return { tone: "sell", text };
  return { tone: "hold", text };
}

const TONE_CLASS = {
  "strong-buy":  "bg-[var(--reco-buy-bg)] text-[var(--reco-buy-fg)] ring-2 ring-[var(--reco-buy-bg)]",
  "buy":         "bg-[var(--reco-buy-bg)] text-[var(--reco-buy-fg)]",
  "hold":        "bg-[var(--reco-hold-bg)] text-[var(--reco-hold-fg)]",
  "sell":        "bg-[var(--reco-sell-bg)] text-[var(--reco-sell-fg)]",
  "strong-sell": "bg-[var(--reco-sell-bg)] text-[var(--reco-sell-fg)] ring-2 ring-[var(--reco-sell-bg)]",
};

export function RecommendationTag({ raw, size = "md" }: { raw: string; size?: "sm" | "md" }) {
  const { tone, text } = parseRecommendation(raw);
  return (
    <span className={cn(
      "inline-flex items-center justify-center rounded-md font-medium",
      size === "sm" ? "text-xs h-6 px-2.5" : "text-sm h-8 px-3",
      TONE_CLASS[tone],
    )}>
      {text}
    </span>
  );
}
