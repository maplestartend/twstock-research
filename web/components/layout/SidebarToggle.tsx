"use client";

import { Icon } from "@/components/primitives/Icon";
import { useSidebar } from "./SidebarProvider";

/** Hamburger 按鈕：mobile 顯示在 Topbar 左側，點擊開啟側邊抽屜。 */
export function SidebarToggle() {
  const { toggle, open } = useSidebar();
  return (
    <button
      type="button"
      onClick={toggle}
      className="lg:hidden -ml-2 p-2 rounded-md hover:bg-subtle text-[var(--text-secondary)]"
      aria-label="開啟選單"
      aria-expanded={open}
    >
      <Icon name="menu" size={22} />
    </button>
  );
}
