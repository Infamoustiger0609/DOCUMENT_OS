import type { Metadata } from "next";

import { fraunces, plexSans } from "@/lib/fonts";
import { AuthProvider } from "@/lib/auth-context";

import "./globals.css";

const SITE_TITLE = "DocumentOS AI — Document Intelligence for Indian CA & Law Firms";
const SITE_DESCRIPTION =
  "Upload contracts, invoices, and GST filings. DocumentOS AI automatically OCRs, classifies, and extracts structured data, tracks deadlines, and lets you ask an AI copilot questions across your entire document registry — built for GSTIN/PAN validation and Indian compliance.";

// Applies site-wide so the public landing page at "/" (not the login screen)
// is what search engines index and what gets shown when a link to this app
// is shared — see CLAUDE.md's Frontend pages section. The favicon/app icon
// itself needs no entry here: app/icon.svg is picked up automatically via
// Next.js's file-based metadata convention.
export const metadata: Metadata = {
  title: SITE_TITLE,
  description: SITE_DESCRIPTION,
  openGraph: {
    title: SITE_TITLE,
    description: SITE_DESCRIPTION,
    type: "website",
    siteName: "DocumentOS AI",
  },
  twitter: {
    card: "summary",
    title: SITE_TITLE,
    description: SITE_DESCRIPTION,
  },
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
