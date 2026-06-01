import { QueryClientProvider } from "@/components/query-client-provider";

export default function ShareLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <QueryClientProvider>
      {children}
    </QueryClientProvider>
  );
}
