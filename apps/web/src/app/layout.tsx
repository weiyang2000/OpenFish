import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "知潮 SaaS Console",
  description: "面向知潮引擎、爬虫与报告的 SaaS 控制台",
  icons: {
    icon: "/icon.svg"
  }
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
