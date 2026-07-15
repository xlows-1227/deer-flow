import type { Metadata } from "next";

import { QueryClientProvider } from "@/components/query-client-provider";

export const metadata: Metadata = {
  title: "已发布页面",
  robots: {
    index: false,
    follow: false,
  },
};

export default function PublishedHtmlLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return <QueryClientProvider>{children}</QueryClientProvider>;
}
