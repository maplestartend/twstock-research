import { PageHeader } from "@/components/primitives/PageHeader";
import {
  KpiRowSkeleton,
  TableSkeleton,
  ListSkeleton,
} from "@/components/primitives/Skeleton";

export default function DashboardLoading() {
  return (
    <div className="p-4 lg:p-8 flex flex-col gap-8 max-w-[1600px] mx-auto">
      <PageHeader title="今日戰情室" icon="dashboard" description="開工具第一眼看的整合資訊" />
      <KpiRowSkeleton count={4} hero />
      <section className="grid grid-cols-1 xl:grid-cols-[1.6fr_1fr] gap-6">
        <TableSkeleton rows={4} cols={6} />
        <ListSkeleton rows={4} />
      </section>
      <section className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <ListSkeleton rows={5} />
        <ListSkeleton rows={5} />
        <ListSkeleton rows={5} />
      </section>
    </div>
  );
}
