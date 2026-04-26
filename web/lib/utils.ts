import clsx, { type ClassValue } from "clsx";

/** Tailwind className 合併。目前純 clsx wrapper（無 tailwind-merge）。
 * 保留這個 alias 而非全 codebase 改 import clsx：30+ 檔案大規模 rename 風險高，
 * 且 cn 命名讓 Tailwind code 可讀性比 clsx 略好。 */
export function cn(...inputs: ClassValue[]): string {
  return clsx(inputs);
}
