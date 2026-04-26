import {
  apiGet,
  apiGetOptional,
  type PresetListResponse,
  type TunerBreakdownResponse,
  type VisibleKeysResponse,
} from "@/lib/api";
import { PageHeader } from "@/components/primitives/PageHeader";
import { EmptyState } from "@/components/primitives/EmptyState";
import { BackendDownError } from "@/components/primitives/BackendDownError";
import { WeightTunerClient } from "./client";

export const revalidate = 0;

export default async function WeightTunerPage() {
  let data: TunerBreakdownResponse;
  let presets: PresetListResponse;
  let visibleKeys: VisibleKeysResponse;
  try {
    [data, presets, visibleKeys] = await Promise.all([
      apiGet<TunerBreakdownResponse>("/api/weight-tuner/breakdown", { noCache: true }),
      apiGet<PresetListResponse>("/api/weight-tuner/presets", { noCache: true }),
      // visible-keys 是純常數；給個 fallback 以防後端老版本沒這個 endpoint
      apiGetOptional<VisibleKeysResponse>("/api/weight-tuner/presets/visible-keys", { noCache: true }).then(
        (v) => v ?? { short: [], mid: [], long: [] },
      ),
    ]);
  } catch (e) {
    return <BackendDownError error={e} pageTitle="權重調優" />;
  }

  return (
    <div className="p-4 lg:p-8 flex flex-col gap-6 max-w-[1600px] mx-auto">
      <PageHeader
        title="權重調優"
        icon="tune"
        description="調整 19 個子指標的影響度（權重越高該指標越重要），即時看自選股的「新評分 vs 預設」。不知道從哪開始？先點「主題式預設」一鍵套用一組現成風格。"
        extra={<>所有計算都在前端進行，怎麼拉都不會弄壞系統。滿意後可<b>「儲存為我的 Preset」</b>下次回來一鍵還原。</>}
      />

      {data.stocks.length === 0 ? (
        <EmptyState tone="secondary" className="leading-relaxed">
          自選股沒有可評分的資料。可能原因：<br />
          ① 還沒加自選股 → 到「自選股管理」加幾檔 <br />
          ② 自選股都不滿 60 天歷史 → 跑 <code className="font-mono text-[11px] px-1 py-0.5 rounded bg-subtle">python -m scripts.market_update --days 260</code> 補資料
        </EmptyState>
      ) : (
        <WeightTunerClient data={data} initialPresets={presets} visibleKeys={visibleKeys} />
      )}
    </div>
  );
}
