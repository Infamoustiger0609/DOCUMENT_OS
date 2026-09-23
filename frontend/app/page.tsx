"use client";

import {
  ArrowRight,
  Calendar,
  CalendarClock,
  CheckCircle2,
  File,
  IndianRupee,
  Percent,
  ScanSearch,
  ShieldCheck,
  Sparkles,
  UploadCloud,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { useEffect } from "react";

import { buttonVariants } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";

// Public marketing landing page — see CLAUDE.md's Frontend pages section.
// Took over "/" from the authenticated dashboard (moved to "/home", see
// CLAUDE.md's Navigation section) so an anonymous visitor gets a real
// product page instead of being bounced straight to the login screen.
// No auth, no data fetching beyond the fire-and-forget warm-up ping below —
// every word on this page is static copy, honest about what the product
// actually does (see CLAUDE.md's Categories/India-specific document
// intelligence/Analytics sections for what backs each claim here).

const HOW_IT_WORKS: { icon: LucideIcon; title: string; description: string }[] = [
  {
    icon: UploadCloud,
    title: "Upload",
    description: "Drop in a PDF, scan, or photo — contracts, invoices, GST filings, purchase orders.",
  },
  {
    icon: ScanSearch,
    title: "Extract",
    description:
      "OCR and classification run automatically, then structured fields (parties, amounts, GSTINs, due dates) are pulled out.",
  },
  {
    icon: Calendar,
    title: "Track deadlines",
    description: "Every renewal, due date, and filing deadline lands on one registry, sorted by urgency.",
  },
  {
    icon: Sparkles,
    title: "Ask Copilot",
    description: "Ask questions in plain English across your whole document registry — not just one file at a time.",
  },
];

const INDIA_FEATURES: { icon: LucideIcon; title: string; description: string }[] = [
  {
    icon: ShieldCheck,
    title: "Real GSTIN & PAN validation",
    description:
      "Not just a format check — a genuine checksum validation (the same ISO 7064 algorithm GSTINs are built on), so a mistyped or fabricated number gets flagged, not just accepted.",
  },
  {
    icon: IndianRupee,
    title: "Lakh & crore aware",
    description:
      '"₹5,40,000" or "five lakh forty thousand rupees only" — parsed and displayed the way Indian invoices actually write amounts, not forced into a western thousand/million format.',
  },
  {
    icon: Percent,
    title: "CGST / SGST / IGST split",
    description:
      "Tax breakdowns are automatically read and classified as intra-state or inter-state, not left as an undifferentiated tax total.",
  },
  {
    icon: CalendarClock,
    title: "GST-specific deadlines",
    description:
      "GSTR filings and other GST-specific compliance dates are tracked as their own category, alongside ordinary contract renewals and invoice due dates.",
  },
];

function PlaceholderIllustration({ icon: Icon }: { icon: LucideIcon }) {
  return (
    <div className="flex aspect-video w-full flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-line bg-paper">
      <Icon className="h-6 w-6 text-muted" strokeWidth={1.5} />
      <span className="text-xs text-muted">Screenshot placeholder</span>
    </div>
  );
}

export default function LandingPage() {
  // Fire-and-forget warm-up ping: Render's free tier cold-starts the backend
  // after ~15 minutes idle (see CLAUDE.md's Known free-tier limits section).
  // This page is the true first thing an anonymous visitor loads, so pinging
  // /health here — rather than on the login page — gets the cold start
  // moving as early as possible. Deliberately isolated: no loading state, no
  // error surfaced, nothing on this page depends on the ping's outcome.
  useEffect(() => {
    const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
    fetch(`${apiUrl}/health`).catch(() => {});
  }, []);

  return (
    <div className="flex min-h-screen flex-col bg-paper">
      <header className="border-b border-line">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-4 sm:px-10">
          <div className="flex items-center gap-2">
            <span className="flex h-7 w-7 items-center justify-center rounded-md text-ink">
              <File className="h-5 w-5" strokeWidth={1.75} />
            </span>
            <span className="font-serif text-[18px] font-medium text-ink">DocumentOS</span>
          </div>
          <div className="flex items-center gap-2">
            <Link href="/login" className={cn(buttonVariants({ variant: "ghost" }))}>
              Log in
            </Link>
            <Link href="/login" className={cn(buttonVariants({ variant: "default" }))}>
              Get started
            </Link>
          </div>
        </div>
      </header>

      <main className="flex-1">
        {/* Hero */}
        <section className="mx-auto max-w-6xl px-6 py-20 sm:px-10 sm:py-28">
          <div className="mx-auto flex max-w-3xl flex-col items-center gap-6 text-center">
            <span className="inline-flex items-center gap-1.5 rounded-full bg-sidebar-bg px-3 py-1 text-xs font-medium text-ink-soft">
              <Sparkles className="h-3.5 w-3.5" strokeWidth={1.75} />
              AI-powered document intelligence
            </span>
            <h1 className="font-serif text-4xl font-semibold leading-tight text-ink sm:text-5xl">
              Document intelligence built for Indian CA and law firms.
            </h1>
            <p className="max-w-2xl text-base leading-relaxed text-ink-soft sm:text-lg">
              Upload contracts, invoices, and GST filings. DocumentOS AI automatically OCRs and
              classifies each one, extracts the fields that matter, tracks every deadline, and lets
              you ask an AI copilot questions across your entire document registry — not just one
              file at a time.
            </p>
            <Link
              href="/login"
              className={cn(buttonVariants({ variant: "default", size: "lg" }), "mt-2")}
            >
              Get started
              <ArrowRight className="h-4 w-4" strokeWidth={2} />
            </Link>
          </div>
        </section>

        {/* How it works */}
        <section className="border-t border-line bg-paper-raised">
          <div className="mx-auto max-w-6xl px-6 py-16 sm:px-10 sm:py-20">
            <div className="mx-auto max-w-2xl text-center">
              <h2 className="font-serif text-2xl font-semibold text-ink sm:text-3xl">How it works</h2>
              <p className="mt-2 text-sm text-ink-soft sm:text-base">
                From a raw file to a tracked, searchable record in four steps.
              </p>
            </div>
            <div className="mt-10 grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-4">
              {HOW_IT_WORKS.map((step, index) => (
                <Card key={step.title} className="flex flex-col gap-4 p-5">
                  <PlaceholderIllustration icon={step.icon} />
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="flex h-5 w-5 items-center justify-center rounded-full bg-ink text-[10px] font-semibold text-paper">
                        {index + 1}
                      </span>
                      <h3 className="text-sm font-semibold text-ink">{step.title}</h3>
                    </div>
                    <p className="mt-2 text-sm leading-relaxed text-ink-soft">{step.description}</p>
                  </div>
                </Card>
              ))}
            </div>
          </div>
        </section>

        {/* Built for Indian firms */}
        <section className="border-t border-line">
          <div className="mx-auto max-w-6xl px-6 py-16 sm:px-10 sm:py-20">
            <div className="mx-auto max-w-2xl text-center">
              <h2 className="font-serif text-2xl font-semibold text-ink sm:text-3xl">
                Built for Indian firms
              </h2>
              <p className="mt-2 text-sm text-ink-soft sm:text-base">
                Not a generic Western SMB tool with a rupee symbol bolted on — India-specific
                compliance is handled at the core.
              </p>
            </div>
            <div className="mt-10 grid grid-cols-1 gap-6 sm:grid-cols-2">
              {INDIA_FEATURES.map((feature) => {
                const Icon = feature.icon;
                return (
                  <Card key={feature.title} className="flex gap-4 p-5">
                    <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-sidebar-bg text-ink">
                      <Icon className="h-5 w-5" strokeWidth={1.75} />
                    </span>
                    <div>
                      <h3 className="text-sm font-semibold text-ink">{feature.title}</h3>
                      <p className="mt-1.5 text-sm leading-relaxed text-ink-soft">{feature.description}</p>
                    </div>
                  </Card>
                );
              })}
            </div>
          </div>
        </section>

        {/* Closing CTA */}
        <section className="border-t border-line bg-paper-raised">
          <div className="mx-auto flex max-w-6xl flex-col items-center gap-5 px-6 py-16 text-center sm:px-10 sm:py-20">
            <CheckCircle2 className="h-8 w-8 text-filed" strokeWidth={1.5} />
            <h2 className="font-serif text-2xl font-semibold text-ink sm:text-3xl">
              Bring your documents. We&apos;ll keep track of them.
            </h2>
            <Link href="/login" className={cn(buttonVariants({ variant: "default", size: "lg" }))}>
              Get started
              <ArrowRight className="h-4 w-4" strokeWidth={2} />
            </Link>
          </div>
        </section>
      </main>

      <footer className="border-t border-line">
        <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-4 px-6 py-8 text-sm text-ink-soft sm:flex-row sm:px-10">
          <div className="flex items-center gap-2">
            <File className="h-4 w-4 text-ink-soft" strokeWidth={1.75} />
            <span className="font-serif text-sm font-medium text-ink">DocumentOS</span>
          </div>
          <span>© {new Date().getFullYear()} DocumentOS AI. All rights reserved.</span>
        </div>
      </footer>
    </div>
  );
}
