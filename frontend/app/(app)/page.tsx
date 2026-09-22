"use client";

import { ArrowRight } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { Card } from "@/components/ui/card";
import { useAuth } from "@/lib/auth-context";
import { cn } from "@/lib/utils";

import {
  DEFAULT_DUE_SOON_THRESHOLD_DAYS,
  DocumentRow,
  formatDate,
  formatRelativeDeadline,
  getDeadlineUrgency,
} from "./documents/types";

// Phase 27 — replaces the old bare backend-health-check page (see CLAUDE.md's
// Navigation section). A glanceable summary only: stat tiles plus two short
// lists, each linking out to the real full view (Documents/Tasks) rather than
// trying to be a second copy of either table.
const RECENT_LIMIT = 5;
const UPCOMING_LIMIT = 5;

function StatTile({
  label,
  count,
  dotClass,
  valueClass,
}: {
  label: string;
  count: number;
  dotClass: string;
  valueClass: string;
}) {
  return (
    <Card className="flex flex-col gap-3 px-5 py-4">
      <div className="flex items-center gap-2">
        <span className={cn("h-2 w-2 rounded-full", dotClass)} />
        <span className="text-xs font-medium uppercase tracking-wide text-ink-soft">{label}</span>
      </div>
      <span className={cn("font-serif text-3xl font-semibold tabular-nums", valueClass)}>{count}</span>
    </Card>
  );
}

function SummaryCard({
  title,
  viewAllHref,
  emptyMessage,
  children,
}: {
  title: string;
  viewAllHref: string;
  emptyMessage: string;
  children: React.ReactNode;
}) {
  return (
    <Card className="flex flex-col overflow-hidden">
      <div className="flex items-center justify-between border-b border-line px-5 py-3.5">
        <h2 className="text-sm font-semibold text-ink">{title}</h2>
        <Link
          href={viewAllHref}
          className="flex items-center gap-1 text-xs font-medium text-ink-soft hover:text-ink"
        >
          View all
          <ArrowRight className="h-3 w-3" strokeWidth={2} />
        </Link>
      </div>
      {children ?? <p className="px-5 py-6 text-sm text-ink-soft">{emptyMessage}</p>}
    </Card>
  );
}

function DocumentRowLink({
  doc,
  trailing,
}: {
  doc: DocumentRow;
  trailing: React.ReactNode;
}) {
  return (
    <Link
      href={`/documents/${doc.id}`}
      className="flex items-center justify-between gap-3 px-5 py-3 text-sm transition-colors hover:bg-sidebar-bg/40"
    >
      <div className="min-w-0 flex-1">
        <p className="truncate text-ink">{doc.filename}</p>
        <p className="truncate text-xs text-ink-soft">{doc.category ?? "Uncategorized"}</p>
      </div>
      <span className="shrink-0">{trailing}</span>
    </Link>
  );
}

export default function HomePage() {
  const { authFetch, user } = useAuth();
  const thresholdDays = user?.due_soon_threshold_days ?? DEFAULT_DUE_SOON_THRESHOLD_DAYS;
  const [documents, setDocuments] = useState<DocumentRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    authFetch("/documents")
      .then((res) => {
        if (!res.ok) throw new Error("Failed to load documents");
        return res.json();
      })
      .then((data: DocumentRow[]) => {
        if (!cancelled) setDocuments(data);
      })
      .catch(() => {
        if (!cancelled) setError("Could not load your documents from the backend.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [authFetch]);

  const stats = useMemo(() => {
    let overdue = 0;
    let dueSoon = 0;
    for (const doc of documents) {
      const urgency = getDeadlineUrgency(doc.deadline_date, thresholdDays);
      if (urgency === "overdue") overdue += 1;
      else if (urgency === "upcoming") dueSoon += 1;
    }
    return { total: documents.length, overdue, dueSoon };
  }, [documents, thresholdDays]);

  const recentDocuments = useMemo(
    () => [...documents].sort((a, b) => b.upload_date.localeCompare(a.upload_date)).slice(0, RECENT_LIMIT),
    [documents]
  );

  // Deliberately excludes overdue deadlines — those already have their own
  // stat tile above; this list is "what's coming up next", not "what's wrong".
  const upcomingDeadlines = useMemo(() => {
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    return documents
      .filter((doc) => doc.deadline_date && new Date(doc.deadline_date) >= today)
      .sort((a, b) => (a.deadline_date ?? "").localeCompare(b.deadline_date ?? ""))
      .slice(0, UPCOMING_LIMIT);
  }, [documents]);

  return (
    <div className="flex flex-col gap-8 px-10 py-10">
      <div>
        <h1 className="font-serif text-2xl font-semibold text-ink">
          Welcome back{user?.name ? `, ${user.name.split(" ")[0]}` : ""}
        </h1>
        <p className="mt-1 text-sm text-ink-soft">Here&apos;s what&apos;s happening across your registry.</p>
      </div>

      {error && <p className="text-sm text-overdue">{error}</p>}

      {loading ? (
        <p className="text-sm text-ink-soft">Loading...</p>
      ) : (
        <>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <StatTile label="Total documents" count={stats.total} dotClass="bg-ink-soft" valueClass="text-ink" />
            <StatTile label="Overdue" count={stats.overdue} dotClass="bg-overdue" valueClass="text-overdue" />
            <StatTile
              label={`Due within ${thresholdDays} days`}
              count={stats.dueSoon}
              dotClass="bg-due-soon"
              valueClass="text-due-soon"
            />
          </div>

          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            <SummaryCard title="Recently uploaded" viewAllHref="/documents" emptyMessage="No documents yet.">
              {recentDocuments.length > 0 && (
                <div className="divide-y divide-line">
                  {recentDocuments.map((doc) => (
                    <DocumentRowLink
                      key={doc.id}
                      doc={doc}
                      trailing={
                        <span className="text-xs tabular-nums text-ink-soft">{formatDate(doc.upload_date)}</span>
                      }
                    />
                  ))}
                </div>
              )}
            </SummaryCard>

            <SummaryCard title="Nearest upcoming deadlines" viewAllHref="/tasks" emptyMessage="Nothing coming up.">
              {upcomingDeadlines.length > 0 && (
                <div className="divide-y divide-line">
                  {upcomingDeadlines.map((doc) => (
                    <DocumentRowLink
                      key={doc.id}
                      doc={doc}
                      trailing={
                        <span className="text-xs font-medium tabular-nums text-due-soon">
                          {formatRelativeDeadline(doc.deadline_date, thresholdDays)}
                        </span>
                      }
                    />
                  ))}
                </div>
              )}
            </SummaryCard>
          </div>
        </>
      )}
    </div>
  );
}
