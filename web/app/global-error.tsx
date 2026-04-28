"use client";

import { useEffect } from "react";

// 最外層 fallback：layout.tsx 自己 throw 時 (rare) 才會走到這裡。
// global-error 必須包 html / body 因為它取代整個 root layout。
export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error("[global error]", error);
  }, [error]);

  return (
    <html lang="zh-Hant">
      <body style={{ fontFamily: "system-ui, sans-serif", padding: "2rem", maxWidth: "640px", margin: "0 auto" }}>
        <h1 style={{ fontSize: "1.5rem", fontWeight: 700 }}>應用程式發生未預期錯誤</h1>
        <p style={{ marginTop: "0.75rem", color: "#475569" }}>
          這通常代表 layout 或全域 provider 出問題。試試重新整理；若仍無法解決，請查看 FastAPI / Next.js 視窗的 log。
        </p>
        <pre style={{
          marginTop: "1rem",
          padding: "0.75rem",
          background: "#f1f5f9",
          color: "#0f172a",
          borderRadius: "6px",
          fontSize: "0.75rem",
          overflowX: "auto",
        }}>{error.message}{error.digest ? `\n\ndigest: ${error.digest}` : ""}</pre>
        <button
          type="button"
          onClick={() => reset()}
          style={{
            marginTop: "1rem",
            padding: "0.5rem 1rem",
            border: "none",
            borderRadius: "6px",
            background: "#0ea5e9",
            color: "#fff",
            cursor: "pointer",
          }}
        >
          重試
        </button>
      </body>
    </html>
  );
}
