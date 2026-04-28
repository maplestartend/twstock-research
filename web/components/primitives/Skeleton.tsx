import { cn } from "@/lib/utils";

export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      aria-hidden
      className={cn(
        "animate-pulse rounded bg-[var(--border-default)]/40",
        className,
      )}
    />
  );
}

export function KpiSkeleton({ size = "md" }: { size?: "sm" | "md" | "lg" }) {
  const h = size === "lg" ? "h-[112px]" : size === "sm" ? "h-[72px]" : "h-[88px]";
  return (
    <div className={cn("rounded-xl border border-[var(--border-default)] bg-surface p-4 flex flex-col gap-3", h)}>
      <Skeleton className="h-3 w-20" />
      <Skeleton className="h-7 w-32" />
      <Skeleton className="h-3 w-16" />
    </div>
  );
}

export function KpiRowSkeleton({ count = 4, hero = false }: { count?: number; hero?: boolean }) {
  return (
    <section className="grid grid-cols-2 lg:grid-cols-4 xl:grid-cols-6 gap-4">
      {hero && (
        <div className="col-span-2">
          <KpiSkeleton size="lg" />
        </div>
      )}
      {Array.from({ length: count }).map((_, i) => (
        <KpiSkeleton key={i} />
      ))}
    </section>
  );
}

export function TableSkeleton({ rows = 6, cols = 6 }: { rows?: number; cols?: number }) {
  return (
    <div className="rounded-xl border border-[var(--border-default)] bg-surface overflow-hidden">
      <div className="border-b border-[var(--border-default)] bg-subtle px-4 py-3 flex gap-4">
        {Array.from({ length: cols }).map((_, i) => (
          <Skeleton key={i} className="h-3 flex-1" />
        ))}
      </div>
      <div className="divide-y divide-[var(--border-default)]">
        {Array.from({ length: rows }).map((_, r) => (
          <div key={r} className="px-4 py-3 flex gap-4">
            {Array.from({ length: cols }).map((_, c) => (
              <Skeleton key={c} className="h-4 flex-1" />
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

export function ListSkeleton({ rows = 5 }: { rows?: number }) {
  return (
    <ul className="rounded-xl border border-[var(--border-default)] bg-surface overflow-hidden">
      {Array.from({ length: rows }).map((_, i) => (
        <li key={i} className="flex items-center gap-3 h-12 px-4 border-b border-[var(--border-default)] last:border-0">
          <Skeleton className="h-3 w-12" />
          <Skeleton className="h-3 flex-1" />
          <Skeleton className="h-3 w-16" />
        </li>
      ))}
    </ul>
  );
}

export function CardSkeleton({ className }: { className?: string }) {
  return (
    <div className={cn("rounded-xl border border-[var(--border-default)] bg-surface p-4 flex flex-col gap-2", className)}>
      <Skeleton className="h-3 w-24" />
      <Skeleton className="h-5 w-full" />
      <Skeleton className="h-3 w-3/4" />
    </div>
  );
}
