/**
 * Client-side 請求可用相對路徑（交給 Next rewrites）；server-side fetch 需要絕對 URL。
 */
export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "";
export const API_BASE_SERVER = process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000";
