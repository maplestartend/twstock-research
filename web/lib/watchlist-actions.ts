"use server";

import { revalidatePath } from "next/cache";

const BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000";

/**
 * Toggle 一檔股票的自選狀態。
 * 為什麼要 server action：純 client-side fetch + router.refresh() 只動當前 route 的 RSC payload，
 * 不會 invalidate 其他頁面的 Next.js Data Cache（apiGet 預設 revalidate: 60）。
 * 這裡 add/remove 完呼叫 revalidatePath('/', 'layout') 把整層 fetch cache 清掉，
 * 之後切到 /watchlist、/、/dividend-calendar 都會重抓一次最新自選清單。
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
    // 整個 layout 的 fetch cache 都失效，下次 navigate 會重新抓
    revalidatePath("/", "layout");
    return { ok: true };
  } catch (e) {
    return { ok: false, error: (e as Error).message };
  }
}
