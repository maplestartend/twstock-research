"use server";

import { revalidateTag } from "next/cache";
import { API_BASE_SERVER } from "@/lib/api-base";

const BASE = API_BASE_SERVER;

/**
 * Toggle 一檔股票的自選狀態。
 *
 * 用 revalidateTag('watchlist') 而非舊版 revalidatePath('/', 'layout')：後者會把整層
 * Next.js Data Cache 全部清掉（首頁 9 支、雷達、除權息、所有頁面下次 navigate 都重抓），
 * 連按 5 個星號 = 5 次全站 refetch。改 tag 後只清打 watchlist + holdings 的 endpoint，
 * 其餘 cache 留在原地。需要被清的頁面 fetch 必須帶 `tags: ['watchlist']`（見 apiGet 用法）。
 */
export async function toggleWatchlistAction(
  stockId: string,
  add: boolean,
): Promise<{ ok: true } | { ok: false; error: string }> {
  try {
    const url = add
      ? `${BASE}/api/watchlist`
      : `${BASE}/api/watchlist/${encodeURIComponent(stockId)}`;
    const res = await fetch(url, {
      method: add ? "POST" : "DELETE",
      headers: add ? { "Content-Type": "application/json" } : undefined,
      body: add ? JSON.stringify({ stock_id: stockId }) : undefined,
      cache: "no-store",
    });
    if (!res.ok && !(res.status === 404 && !add)) {
      // 404 on remove 視為 idempotent OK（已經不在清單）
      const detail = await res.text();
      return { ok: false, error: `HTTP ${res.status} ${detail || res.statusText}` };
    }
    // 只清打過 watchlist tag 的 endpoint 快取
    revalidateTag("watchlist");
    return { ok: true };
  } catch (e) {
    return { ok: false, error: (e as Error).message };
  }
}
