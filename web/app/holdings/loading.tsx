import { PageHeader } from "@/components/primitives/PageHeader";
import {
  KpiRowSkeleton,
  TableSkeleton,
} from "@/components/primitives/Skeleton";

export default function HoldingsLoading() {
  return (
    <div className="p-4 lg:p-8 flex flex-col gap-8 max-w-[1600px] mx-auto">
      <PageHeader
        title="我的持股"
        icon="account_balance_wallet"
        description="持股總覽、新增/刪除交易、已實現損益"
      />
      <KpiRowSkeleton count={4} />
      <TableSkeleton rows={6} cols={9} />
    </div>
  );
}
