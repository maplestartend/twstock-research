/**
 * 回測情景預設 — 一鍵套用一組合理參數，避免使用者面對一堆數字。
 *
 * 共三類：
 * - BACKTEST_SCENARIOS：策略回測 / 投組回測共用（短線/波段/長期）
 * - EVENT_SCENARIOS   ：除權息事件回測（提前/經典/貼息）
 */

export type BacktestScenarioKey = "short" | "swing" | "long";

export type BacktestScenario = {
  label: string;
  desc: string;
  icon: string;
  cfg: {
    entry: number;
    exit: number;
    sl: number;
    tp: number;
    maxHold: number;
    slippage: number;
    lookback: number;
  };
};

export const BACKTEST_SCENARIOS: Record<BacktestScenarioKey, BacktestScenario> = {
  short: {
    label: "短線快手",
    desc: "嚴格進場、快速停損，賺快錢",
    icon: "bolt",
    cfg: { entry: 70, exit: 50, sl: 0.05, tp: 0.15, maxHold: 30, slippage: 5, lookback: 500 },
  },
  swing: {
    label: "波段獵人",
    desc: "預設值，平衡型，多數人適用",
    icon: "show_chart",
    cfg: { entry: 65, exit: 40, sl: 0.08, tp: 0.20, maxHold: 60, slippage: 5, lookback: 500 },
  },
  long: {
    label: "長期持有",
    desc: "寬鬆進場、放長一點，少進出",
    icon: "trending_up",
    cfg: { entry: 55, exit: 30, sl: 0.10, tp: 0.30, maxHold: 120, slippage: 5, lookback: 700 },
  },
};

export type EventScenarioKey = "early" | "classic" | "late";

export type EventScenario = {
  label: string;
  desc: string;
  icon: string;
  entry: number;
  exit: number;
};

export const EVENT_SCENARIOS: Record<EventScenarioKey, EventScenario> = {
  early:   { label: "提前布局", desc: "事件前 10 日進場，事件後 5 日出", icon: "schedule",    entry: -10, exit: 5 },
  classic: { label: "經典套息", desc: "事件前 5 日進、事件後 10 日出",   icon: "celebration", entry: -5,  exit: 10 },
  late:    { label: "貼息回補", desc: "事件後 1 日進、事件後 30 日出",   icon: "redo",        entry: 1,   exit: 30 },
};
