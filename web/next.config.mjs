/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  experimental: {
    // recharts barrel re-exports 整包 + treeshake 不掉，RSC payload 會多帶
    // 不必要的 chart 元件。optimizePackageImports 會把 `import { LineChart } from "recharts"`
    // 自動轉成具體子模組路徑，常見可省 40~60% 的 recharts client bundle。
    optimizePackageImports: ["recharts"],
  },
  async rewrites() {
    // 開發期把 /api/* 代理到 FastAPI，避免 CORS；正式環境請由 Nginx 反代
    return [
      {
        source: "/api/:path*",
        destination: "http://127.0.0.1:8000/api/:path*",
      },
    ];
  },
};

export default nextConfig;
