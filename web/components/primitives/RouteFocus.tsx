"use client";

import { useEffect, useRef } from "react";
import { usePathname } from "next/navigation";

/**
 * SPA 換頁無障礙：Next App Router 換頁不會 reload，焦點仍停在剛點的連結上，
 * 鍵盤 / 螢幕報讀器使用者不會被告知「頁面換了」。本元件在 pathname 變化後，把焦點
 * 移到主內容區的 <h1>（PageHeader 已設 tabIndex=-1），讓報讀器朗讀新頁標題。
 *
 * - 初次載入不搶焦點（避免干擾正常的 tab 起點 / Skip-link）。
 * - 找不到 main h1 的頁面：退而求其次設一次 tabindex 再 focus（防呆）。
 */
export function RouteFocus() {
  const pathname = usePathname();
  const first = useRef(true);

  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    const h1 = document.querySelector("main h1") as HTMLElement | null;
    if (!h1) return;
    if (!h1.hasAttribute("tabindex")) h1.setAttribute("tabindex", "-1");
    h1.focus({ preventScroll: false });
  }, [pathname]);

  return null;
}
