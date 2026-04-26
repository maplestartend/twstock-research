import { Icon } from "./Icon";

export function SectionTitle({ children, icon }: { children: React.ReactNode; icon?: string }) {
  return (
    <h2 className="text-base font-semibold text-[var(--text-primary)] inline-flex items-center gap-2">
      {icon && <Icon name={icon} size={20} className="text-[var(--brand-500)]" />}
      {children}
    </h2>
  );
}
