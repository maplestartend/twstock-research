"use client";

import { useEffect, useRef, useState } from "react";
import { Icon } from "./Icon";

/** 包在水平捲動表格外層：偵測是否可向右捲、若可則顯示一個箭頭 + 漸層遮罩。
 * 主要解決 mobile 上「表格其實能左右捲，但用戶看不到提示，以為欄位被砍掉」的問題
 * (UIUX 審查 P0-4)。
 *
 * 使用方式：
 *   <TableScrollHint>
 *     <div className="overflow-x-auto"> ... <table /> ...</div>
 *   </TableScrollHint>
 */
export function TableScrollHint({ children }: { children: React.ReactNode }) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const [canRight, setCanRight] = useState(false);
  const [canLeft, setCanLeft] = useState(false);

  useEffect(() => {
    const wrap = wrapRef.current;
    if (!wrap) return;
    const scroller = wrap.querySelector<HTMLElement>("[data-scroll]") ?? wrap.firstElementChild as HTMLElement | null;
    if (!scroller) return;

    const update = () => {
      const sl = scroller.scrollLeft;
      const sw = scroller.scrollWidth;
      const cw = scroller.clientWidth;
      setCanLeft(sl > 1);
      setCanRight(sl + cw < sw - 1);
    };
    update();
    scroller.addEventListener("scroll", update, { passive: true });
    const ro = new ResizeObserver(update);
    ro.observe(scroller);
    return () => {
      scroller.removeEventListener("scroll", update);
      ro.disconnect();
    };
  }, []);

  return (
    <div ref={wrapRef} className="relative">
      {children}
      {canRight && (
        <div className="pointer-events-none absolute inset-y-0 right-0 w-12 bg-gradient-to-l from-surface to-transparent rounded-r-xl flex items-center justify-end pr-1">
          <Icon name="chevron_right" size={16} className="text-[var(--text-tertiary)] animate-pulse" />
        </div>
      )}
      {canLeft && (
        <div className="pointer-events-none absolute inset-y-0 left-0 w-8 bg-gradient-to-r from-surface to-transparent rounded-l-xl" />
      )}
    </div>
  );
}
