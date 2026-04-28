"use client";

import dynamic from "next/dynamic";

// CommandPalette 只在 Cmd/Ctrl+K 或點搜尋按鈕時才用得上，沒必要在首屏就 hydrate。
// ssr:false + dynamic 把這支 ~272 行 client component 從每頁 First Load JS 中移除。
const CommandPalette = dynamic(
  () => import("./CommandPalette").then((m) => ({ default: m.CommandPalette })),
  { ssr: false },
);

export function CommandPaletteLoader() {
  return <CommandPalette />;
}
