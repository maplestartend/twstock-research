"use client";

import { useEffect } from "react";
import { BackendDownError } from "@/components/primitives/BackendDownError";

// Next.js App Router 的 route-level error boundary：
// client component 在 render / 事件中 throw 會落到這裡，避免使用者看到預設黑屏。
export default function ErrorBoundary({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error("[route error]", error);
  }, [error]);

  return (
    <div className="flex flex-col gap-4 p-8 max-w-[800px] mx-auto">
      <BackendDownError error={error} pageTitle="頁面載入失敗" />
      <button
        type="button"
        onClick={() => reset()}
        className="self-start inline-flex items-center gap-2 h-9 px-4 rounded-md bg-[var(--brand-500)] text-white text-sm font-medium hover:bg-[var(--brand-600)] transition-colors"
      >
        重試
      </button>
    </div>
  );
}
