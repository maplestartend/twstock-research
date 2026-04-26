import type { Metadata } from "next";
import { Noto_Sans_TC, JetBrains_Mono } from "next/font/google";
import "../styles/globals.css";
import { Sidebar } from "@/components/layout/Sidebar";
import { SidebarProvider } from "@/components/layout/SidebarProvider";
import { Topbar } from "@/components/layout/Topbar";
import { CommandPalette } from "@/components/primitives/CommandPalette";

const notoTC = Noto_Sans_TC({
  subsets: ["latin"],
  weight: ["400", "500", "700"],
  variable: "--font-sans-runtime",
  display: "swap",
});
const jetbrains = JetBrains_Mono({
  subsets: ["latin"],
  weight: ["400", "500", "700"],
  variable: "--font-mono-runtime",
  display: "swap",
});

export const metadata: Metadata = {
  title: "台股研究儀表板",
  description: "台股自選股 + 持股 + 雷達命中 + 評分",
};

// 在 hydration 前就套好 data-theme，避免 FOUC。"系統"也會解析成 light/dark 實值
// 以便 Tailwind 的 `dark:` 前綴（我們改成 attribute-based）能正確匹配。
const preThemeScript = `
(function () {
  try {
    var pref = localStorage.getItem('theme');
    var effective = (pref === 'light' || pref === 'dark')
      ? pref
      : (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    document.documentElement.setAttribute('data-theme', effective);
  } catch (e) {
    document.documentElement.setAttribute('data-theme', 'light');
  }
})();
`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-TW" suppressHydrationWarning className={`${notoTC.variable} ${jetbrains.variable}`}>
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link
          rel="stylesheet"
          href="https://fonts.googleapis.com/css2?family=Material+Symbols+Rounded:opsz,wght,FILL,GRAD@20..48,100..700,0..1,-25..200&display=block"
        />
        <script dangerouslySetInnerHTML={{ __html: preThemeScript }} />
      </head>
      <body>
        <SidebarProvider>
          <div className="min-h-screen flex bg-canvas">
            <Sidebar />
            <div className="flex-1 flex flex-col min-w-0">
              <Topbar />
              <main className="flex-1 overflow-x-hidden">
                {children}
              </main>
            </div>
          </div>
          <CommandPalette />
        </SidebarProvider>
      </body>
    </html>
  );
}
