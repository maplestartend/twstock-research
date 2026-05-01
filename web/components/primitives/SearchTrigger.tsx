"use client";

import { useEffect, useState } from "react";
import { Icon } from "@/components/primitives/Icon";

/**
 * Topbar 的搜尋按鈕。點擊時派發 CustomEvent，由 CommandPalette 監聽並打開。
 * 這樣可以避免引入 store 來共享開關狀態。
 */
export function SearchTrigger() {
  const [hotkeyLabel, setHotkeyLabel] = useState("Ctrl");
  useEffect(() => {
    const platform = navigator.platform || "";
    if (/Mac|iPhone|iPad/i.test(platform)) setHotkeyLabel("⌘");
  }, []);

  const handleClick = () => {
    window.dispatchEvent(new CustomEvent("commandpalette:open"));
  };
  return (
    <button
      type="button"
      name="open-search"
      onClick={handleClick}
      className="inline-flex items-center gap-2 h-9 px-3 rounded-md border border-[var(--border-default)] bg-subtle hover:bg-[var(--bg-hover)] text-sm text-[var(--text-tertiary)] transition-colors"
      aria-label={`開啟全域搜尋（${hotkeyLabel}+K）`}
    >
      <Icon name="search" size={16} />
      <span className="hidden md:inline">搜尋...</span>
      <kbd className="hidden md:inline-flex items-center gap-0.5 text-[11px] font-mono px-1.5 py-0.5 rounded border border-[var(--border-default)] bg-surface">
        <span>{hotkeyLabel}</span>K
      </kbd>
    </button>
  );
}
