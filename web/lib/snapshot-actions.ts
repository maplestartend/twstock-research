"use server";

import { revalidateTag } from "next/cache";
import { API_BASE_SERVER } from "@/lib/api-base";
import type { RefreshSnapshotResponse } from "@/lib/api";

export async function refreshSnapshotAction(): Promise<RefreshSnapshotResponse> {
  const res = await fetch(`${API_BASE_SERVER}/api/system/refresh-snapshot`, {
    method: "POST",
    cache: "no-store",
    headers: { "Content-Type": "application/json; charset=utf-8" },
    body: "{}",
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body?.detail) detail = body.detail;
    } catch {}
    throw new Error(detail);
  }
  const data = (await res.json()) as RefreshSnapshotResponse;
  // 所有吃 signal_history / snapshot 狀態的 RSC fetch 走此 tag，手動重算後立即失效。
  revalidateTag("snapshot");
  return data;
}
