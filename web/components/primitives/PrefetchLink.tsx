"use client";

import Link, { type LinkProps } from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useRef, type ReactNode } from "react";

/**
 * 加上 hover 主動 prefetch 的 Link。Next.js `<Link prefetch>` 預設只 prefetch
 * static layout payload；對於 dynamic data fetching（如 /radar?strategy=X 切
 * 策略），仍要點下去才開始抓。Hover 時 router.prefetch(href) 把整條 RSC tree
 * 拉到 client，點擊時近乎瞬間 navigate。
 *
 * 50ms debounce 避免滑鼠掃過一排 chip 觸發每一個的 prefetch。
 */
export function PrefetchLink({
  href,
  children,
  className,
  title,
  scroll,
  ...rest
}: Omit<LinkProps, "href"> & {
  href: string;
  children: ReactNode;
  className?: string;
  title?: string;
}) {
  const router = useRouter();
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const armPrefetch = useCallback(() => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      router.prefetch(href);
    }, 50);
  }, [router, href]);

  const cancelPrefetch = useCallback(() => {
    if (timer.current) {
      clearTimeout(timer.current);
      timer.current = null;
    }
  }, []);

  return (
    <Link
      href={href}
      className={className}
      title={title}
      scroll={scroll}
      onMouseEnter={armPrefetch}
      onMouseLeave={cancelPrefetch}
      onFocus={armPrefetch}
      onBlur={cancelPrefetch}
      {...rest}
    >
      {children}
    </Link>
  );
}
