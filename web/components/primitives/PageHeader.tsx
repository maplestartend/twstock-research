import type { ReactNode } from "react";
import { Icon } from "./Icon";

export type PageHeaderProps = {
  title: string;
  icon: string;
  description?: ReactNode;
  extra?: ReactNode;
};

export function PageHeader({ title, icon, description, extra }: PageHeaderProps) {
  return (
    <section>
      <h1 className="text-[24px] font-bold text-[var(--text-primary)] inline-flex items-center gap-2.5">
        <Icon name={icon} size={28} filled className="text-[var(--brand-500)]" />
        {title}
      </h1>
      {description && (
        <p className="text-sm text-[var(--text-tertiary)] mt-1 leading-relaxed">
          {description}
        </p>
      )}
      {extra && (
        <p className="text-xs text-[var(--text-tertiary)] mt-1 leading-relaxed">
          {extra}
        </p>
      )}
    </section>
  );
}
