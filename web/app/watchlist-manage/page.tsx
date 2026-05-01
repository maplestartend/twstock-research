import { apiGet, type WatchlistEntry } from "@/lib/api";
import { PageHeader } from "@/components/primitives/PageHeader";
import { BackendDownError } from "@/components/primitives/BackendDownError";
import { WatchlistManageClient } from "./client";

export const revalidate = 0;  // 每次進頁面重抓最新自選股

export default async function WatchlistManagePage() {
  let entries: WatchlistEntry[];
  try {
    entries = await apiGet<WatchlistEntry[]>("/api/watchlist", { noCache: true });
  } catch (e) {
    return <BackendDownError error={e} pageTitle="自選股管理" />;
  }
  return (
    <div className="p-4 lg:p-8 flex flex-col gap-8 max-w-[1200px] mx-auto">
      <PageHeader
        title="自選股管理"
        icon="edit_note"
        description="新增 / 刪除自選清單（名稱自動跟隨代號）"
      />
      <WatchlistManageClient initialEntries={entries} />
    </div>
  );
}
