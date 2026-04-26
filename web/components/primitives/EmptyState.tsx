import type { ReactNode } from "react";
import { cn } from "@/lib/utils";
import { Icon } from "./Icon";

export type EmptyStateProps = {
  children: ReactNode;
  size?: "sm" | "md";
  tone?: "tertiary" | "secondary";
  icon?: string | false;     // 預設 "inbox"；傳 false 可關閉
  className?: string;
};

export function EmptyState({
  children,
  size = "md",
  tone = "tertiary",
  icon = "inbox",
  className,
}: EmptyStateProps) {
  return (
    <div
      className={cn(
        "rounded-xl border border-dashed border-[var(--border-default)] text-center text-sm flex flex-col items-center gap-2",
        size === "sm" ? "p-6" : "p-8",
        tone === "secondary" ? "text-[var(--text-secondary)]" : "text-[var(--text-tertiary)]",
        className,
      )}
    >
      {icon !== false && (
        <Icon name={icon} size={24} className="opacity-40" />
      )}
      <div>{children}</div>
    </div>
  );
}
