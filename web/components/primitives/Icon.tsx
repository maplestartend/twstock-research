import { cn } from "@/lib/utils";

export type IconProps = {
  name: string;                                  // Material Symbols 名稱，如 "home"、"trending_up"
  size?: number;                                 // px；控制 opsz 同步變更字型大小
  weight?: 100 | 200 | 300 | 400 | 500 | 600 | 700;
  filled?: boolean;                              // FILL 軸
  grade?: -25 | 0 | 200;                         // GRAD 軸（對比度）
  className?: string;
  label?: string;                                // 若提供則為 role="img" + aria-label；否則 aria-hidden
};

export function Icon({
  name,
  size = 20,
  weight = 400,
  filled = false,
  grade = 0,
  className,
  label,
}: IconProps) {
  return (
    <span
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      className={cn("material-symbols-rounded shrink-0", className)}
      style={{
        fontSize: `${size}px`,
        width: `${size}px`,
        height: `${size}px`,
        fontVariationSettings: `'FILL' ${filled ? 1 : 0}, 'wght' ${weight}, 'GRAD' ${grade}, 'opsz' ${Math.min(48, Math.max(20, size))}`,
      }}
    >
      {name}
    </span>
  );
}
