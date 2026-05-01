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
    <html lang="zh-TW">
      <body
        style={{
          margin: 0,
          minHeight: "100vh",
          fontFamily: "system-ui, sans-serif",
          background: "var(--bg-base, #f8fafc)",
          color: "var(--text-primary, #0f172a)",
        }}
      >
        <main
          style={{
            maxWidth: "720px",
            margin: "0 auto",
            padding: "2.5rem 1.25rem",
          }}
        >
          <section
            style={{
              border: "1px solid var(--border-default, #cbd5e1)",
              borderRadius: "0.75rem",
              background: "var(--bg-surface, #ffffff)",
              padding: "1.25rem",
            }}
          >
            <h1 style={{ fontSize: "1.4rem", lineHeight: 1.35, fontWeight: 700, margin: 0 }}>
              應用程式發生未預期錯誤
            </h1>
            <p style={{ marginTop: "0.75rem", color: "var(--text-secondary, #475569)" }}>
              可能是 layout 或全域 provider 初始化失敗。請先按「重試」，若仍失敗再查看 FastAPI /
              Next.js 視窗 log。
            </p>
            <pre
              style={{
                marginTop: "1rem",
                marginBottom: 0,
                padding: "0.75rem",
                background: "var(--bg-subtle, #f1f5f9)",
                color: "var(--text-primary, #0f172a)",
                borderRadius: "0.5rem",
                fontSize: "0.75rem",
                overflowX: "auto",
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
              }}
            >
              {error.message}
              {error.digest ? `\n\ndigest: ${error.digest}` : ""}
            </pre>
            <button
              type="button"
              onClick={() => reset()}
              style={{
                marginTop: "1rem",
                padding: "0.5rem 1rem",
                border: "1px solid var(--brand-500, #0ea5e9)",
                borderRadius: "0.5rem",
                background: "var(--brand-500, #0ea5e9)",
                color: "#fff",
                cursor: "pointer",
                fontWeight: 600,
              }}
            >
              重試
            </button>
          </section>
        </main>
      </body>
    </html>
  );
}
