"use client";

import {
  ArrowRight,
  Bot,
  Calendar,
  CalendarClock,
  CheckCircle2,
  File,
  FilePlus2,
  Folder,
  IndianRupee,
  Percent,
  PenLine,
  Receipt,
  Send,
  ShieldCheck,
  Sparkles,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useRef } from "react";

import { ChatBubble } from "@/components/chat-bubble";
import { DeadlineRow } from "@/components/deadline-row";
import { DetailCard, FieldRow } from "@/components/field-row";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { DropzoneBox, UploadProgressCard } from "@/components/upload-dropzone";
import { cn } from "@/lib/utils";

import { type DeadlineBucket, type DocumentRow } from "./(app)/documents/types";

// Public marketing landing page — see CLAUDE.md's Public landing page
// section. Took over "/" from the authenticated dashboard (moved to "/home",
// see CLAUDE.md's Navigation section) so an anonymous visitor gets a real
// product page instead of being bounced straight to the login screen.
// No auth, no data fetching beyond the fire-and-forget warm-up ping below —
// every word on this page is static copy, honest about what the product
// actually does (see CLAUDE.md's Categories/India-specific document
// intelligence/Analytics sections for what backs each claim here).
//
// The "How it works" previews below render the app's REAL, real dashboard
// components (DropzoneBox/UploadProgressCard, FieldRow/DetailCard,
// DeadlineRow, ChatBubble — all extracted out of their real pages into
// shared components for exactly this reuse) fed hardcoded, clearly-fictional
// sample data, rather than screenshots or generic icons — so this section
// automatically stays visually in sync with the real app.

function daysFromToday(offsetDays: number): string {
  const date = new Date();
  date.setDate(date.getDate() + offsetDays);
  return date.toISOString().slice(0, 10);
}

function sampleDoc(overrides: Partial<DocumentRow> & Pick<DocumentRow, "id" | "filename">): DocumentRow {
  return {
    category: null,
    upload_date: daysFromToday(-30),
    storage_path: "",
    extracted_json: null,
    deadline_date: null,
    status: "processed",
    error_message: null,
    classification_reasoning: null,
    classification_version: 3,
    ...overrides,
  };
}

const SAMPLE_DEADLINES: { doc: DocumentRow; bucket: DeadlineBucket }[] = [
  {
    doc: sampleDoc({
      id: "sample-1",
      filename: "Lease_Agreement_Renewal.pdf",
      category: "Agreement",
      extracted_json: { party_names: ["Acme Traders Pvt Ltd"] },
      deadline_date: daysFromToday(-6),
    }),
    bucket: "overdue",
  },
  {
    doc: sampleDoc({
      id: "sample-2",
      filename: "GSTR-3B_Filing.pdf",
      category: "GST Filing",
      extracted_json: { gstin: "29ABCDE1234F1Z5" },
      deadline_date: daysFromToday(3),
    }),
    bucket: "due-this-week",
  },
  {
    doc: sampleDoc({
      id: "sample-3",
      filename: "Vendor_Contract_NorthStar.pdf",
      category: "Agreement",
      extracted_json: { party_names: ["NorthStar Logistics"] },
      deadline_date: daysFromToday(90),
    }),
    bucket: "later",
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

const FEATURES: { icon: LucideIcon; title: string; description: string }[] = [
  {
    icon: Folder,
    title: "Registry",
    description:
      "A searchable, filterable home for every document you've ever uploaded, organized by category and status.",
  },
  {
    icon: Calendar,
    title: "Deadlines",
    description:
      "Every renewal, due date, and filing deadline across all your documents in one sorted view, so nothing is tracked in a separate spreadsheet.",
  },
  {
    icon: Sparkles,
    title: "AI Copilot",
    description:
      "Ask questions in plain English across your whole document registry, or about one document at a time — search, summarize, compare, without opening every file.",
  },
  {
    icon: Bot,
    title: "Editing workspace",
    description:
      "Merge, split, compress, convert, and OCR documents directly in the app, plus manage uploaded templates and generated documents separately from your main registry.",
  },
  {
    icon: FilePlus2,
    title: "Document generation",
    description:
      "Upload one sample document once, then generate new ones from it going forward — invoices, agreements, whatever you generate repeatedly.",
  },
  {
    icon: PenLine,
    title: "E-signature",
    description: "Sign generated or uploaded documents without leaving the app.",
  },
];

// Text column fixed-width, preview column flex-1 — stacked full-width rather
// than a 2-up grid, so a real, full-width component like DeadlineRow (built
// for /tasks's full page width) has enough room and never has to truncate
// its content to fit a narrow grid cell.
function StepCard({
  number,
  title,
  description,
  children,
}: {
  number: number;
  title: string;
  description: string;
  children: React.ReactNode;
}) {
  return (
    <Card className="flex flex-col gap-5 p-4 sm:flex-row sm:items-center sm:gap-8 sm:p-6">
      <div className="sm:w-56 sm:shrink-0">
        <div className="flex items-center gap-2">
          <span className="flex h-5 w-5 items-center justify-center rounded-full bg-ink text-[10px] font-semibold text-paper">
            {number}
          </span>
          <h3 className="text-sm font-semibold text-ink">{title}</h3>
        </div>
        <p className="mt-2 text-sm leading-relaxed text-ink-soft">{description}</p>
      </div>
      <div className="min-w-0 flex-1 overflow-hidden rounded-md border border-line bg-paper p-2 sm:p-3">
        {children}
      </div>
    </Card>
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

  // Dummy ref for the non-interactive DropzoneBox preview below — never
  // actually clicked, since `interactive={false}` disables the "Choose
  // file" button and omits the hidden <input> entirely.
  const noopFileInputRef = useRef<HTMLInputElement>(null);

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
            <div className="mt-10 flex flex-col gap-6">
              <StepCard
                number={1}
                title="Upload"
                description="Drop in a PDF, scan, or photo — contracts, invoices, GST filings, purchase orders."
              >
                <div className="flex flex-col gap-3">
                  <DropzoneBox interactive={false} className="py-8" fileInputRef={noopFileInputRef} />
                  <UploadProgressCard progress={62} />
                </div>
              </StepCard>

              <StepCard
                number={2}
                title="Extract"
                description="OCR and classification run automatically, then structured fields (parties, amounts, GSTINs, due dates) are pulled out."
              >
                <div className="flex flex-col gap-3">
                  <div className="flex items-center gap-2">
                    <Receipt className="h-4 w-4 text-ink-soft" strokeWidth={1.75} />
                    <span className="text-xs font-semibold uppercase tracking-wide text-ink-soft">
                      Invoice
                    </span>
                    <span className="ml-auto inline-flex items-center gap-1.5 rounded-full bg-filed/10 px-2 py-0.5 text-xs font-semibold text-filed">
                      <span className="h-1.5 w-1.5 rounded-full bg-filed" />
                      Processed
                    </span>
                  </div>
                  <DetailCard title="Billing details">
                    <FieldRow label="Vendor Name" value="Acme Traders Pvt Ltd" numeric={false} />
                    <FieldRow label="GST Number" value="29ABCDE1234F1Z5" numeric={false} />
                    <FieldRow label="Total Amount" value="₹1,84,000" numeric />
                  </DetailCard>
                </div>
              </StepCard>

              <StepCard
                number={3}
                title="Track deadlines"
                description="Every renewal, due date, and filing deadline lands on one registry, sorted by urgency."
              >
                <div className="pointer-events-none divide-y divide-line rounded-lg border border-line bg-paper-raised">
                  {SAMPLE_DEADLINES.map(({ doc, bucket }) => (
                    <DeadlineRow key={doc.id} doc={doc} bucket={bucket} onOpen={() => {}} thresholdDays={30} />
                  ))}
                </div>
              </StepCard>

              <StepCard
                number={4}
                title="Ask Copilot"
                description="Ask questions in plain English across your whole document registry — not just one file at a time."
              >
                <div className="flex flex-col overflow-hidden rounded-lg border border-line bg-paper-raised">
                  <div className="flex items-center gap-2 border-b border-line px-3.5 py-3">
                    <Sparkles className="h-4 w-4 text-ink" strokeWidth={1.75} />
                    <span className="text-sm font-semibold text-ink">DocumentOS Copilot</span>
                  </div>
                  <div className="flex flex-col gap-3 px-3.5 py-3.5">
                    <ChatBubble role="user" content="Which invoices are overdue?" />
                    <ChatBubble
                      role="assistant"
                      content="Acme Traders Pvt Ltd's invoice (₹1,84,000) is 6 days overdue — nothing else is."
                    />
                  </div>
                  <div className="pointer-events-none flex items-center gap-2 border-t border-line p-3">
                    <Input type="text" placeholder="Ask DocumentOS..." disabled className="flex-1" />
                    <Button type="button" size="icon" disabled>
                      <Send className="h-4 w-4" strokeWidth={1.75} />
                    </Button>
                  </div>
                </div>
              </StepCard>
            </div>
          </div>
        </section>

        {/* Everything in one place */}
        <section className="border-t border-line">
          <div className="mx-auto max-w-6xl px-6 py-16 sm:px-10 sm:py-20">
            <div className="mx-auto max-w-2xl text-center">
              <h2 className="font-serif text-2xl font-semibold text-ink sm:text-3xl">
                Everything in one place
              </h2>
              <p className="mt-2 text-sm text-ink-soft sm:text-base">
                Beyond the four steps above, one app covers the whole document lifecycle.
              </p>
            </div>
            <div className="mt-10 grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
              {FEATURES.map((feature) => {
                const Icon = feature.icon;
                return (
                  <Card key={feature.title} className="flex flex-col gap-3 p-5">
                    <span className="flex h-9 w-9 items-center justify-center rounded-md bg-sidebar-bg text-ink">
                      <Icon className="h-4 w-4" strokeWidth={1.75} />
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

        {/* Built for Indian firms */}
        <section className="border-t border-line bg-paper-raised">
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
        <section className="border-t border-line">
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
