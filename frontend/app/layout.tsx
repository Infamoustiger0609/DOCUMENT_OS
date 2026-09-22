import type { Metadata } from "next";

import { fraunces, plexSans } from "@/lib/fonts";
import { AuthProvider } from "@/lib/auth-context";

import "./globals.css";

export const metadata: Metadata = {
  title: "DocumentOS AI",
  description: "DocumentOS AI",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className={`${fraunces.variable} ${plexSans.variable}`}>
      <body className="min-h-screen bg-paper font-sans text-ink antialiased">
        <AuthProvider>{children}</AuthProvider>
      </body>
    </html>
  );
}
