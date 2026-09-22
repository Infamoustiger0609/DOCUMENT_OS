"use client";

import { ChevronRight } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import { useAuth } from "@/lib/auth-context";
import { cn } from "@/lib/utils";

import {
  type DeadlineBucket,
  DEFAULT_DUE_SOON_THRESHOLD_DAYS,
  DocumentRow,
  formatDate,
  formatRelativeDeadline,
  getDeadlineBucket,
  getPartySummary,
} from "../documents/types";

const BUCKET_ORDER: DeadlineBucket[] = ["overdue", "due-this-week", "due-this-month"];

// "Due this month" would be misleading once the threshold is configurable (e.g.
// 60 days is ~2 months, 7 days collapses this bucket entirely) — "Due soon"
// stays accurate regardless of the chosen threshold.
const BUCKET_TITLES: Record<DeadlineBucket, string> = {
  overdue: "Overdue",
  "due-this-week": "Due this week",
  "due-this-month": "Due soon",
  later: "Later",
};

const BUCKET_DOT_CLASSES: Record<DeadlineBucket, string> = {
  overdue: "bg-overdue",
  "due-this-week": "bg-due-soon",
  "due-this-month": "bg-ink-soft",
  later: "bg-ink-soft",
};

const BUCKET_LABEL_CLASSES: Record<DeadlineBucket, string> = {
  overdue: "text-overdue",
  "due-this-week": "text-due-soon",
  "due-this-month": "text-ink-soft",
  later: "text-ink-soft",
};

const BUCKET_PILL_CLASSES: Record<DeadlineBucket, string> = {
  overdue: "bg-overdue/10 text-overdue",
  "due-this-week": "bg-due-soon/10 text-due-soon",
  "due-this-month": "bg-sidebar-bg text-ink-soft",
  later: "bg-sidebar-bg text-ink-soft",
};

function DeadlineRow({
  doc,
  bucket,
  onOpen,
  thresholdDays,
}: {
  doc: DocumentRow;
  bucket: DeadlineBucket;
  onOpen: (doc: DocumentRow) => void;
  thresholdDays: number;
}) {
  const relative = formatRelativeDeadline(doc.deadline_date, thresholdDays);
  const party = getPartySummary(doc);
  const dateLabel =
    bucket === "overdue"
      ? `Expired ${formatDate(doc.deadline_date)}`
      : `Due ${formatDate(doc.deadline_date)}`;

  return (
    <button
      type="button"
      onClick={() => onOpen(doc)}
      className="flex w-full items-center gap-4 px-5 py-3.5 text-left transition-colors hover:bg-sidebar-bg/40"
    >
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-semibold text-ink">{doc.filename}</p>
        <p className="truncate text-xs text-ink-soft">
          {doc.category ?? "Uncategorized"}
          {party && ` · ${party}`}
        </p>
      </div>
      <span className="hidden w-36 shrink-0 text-sm tabular-nums text-ink sm:block">
        {dateLabel}
      </span>
      <span className="flex w-32 shrink-0 justify-end">
        <span
          className={cn(
            "inline-flex items-center rounded-full px-2.5 py-1 text-xs font-semibold",
            BUCKET_PILL_CLASSES[bucket]
          )}
        >
          {relative}
        </span>
      </span>
      <ChevronRight className="h-4 w-4 shrink-0 text-ink-soft" strokeWidth={2} />
    </button>
  );
}

function BucketSection({
  bucket,
  documents,
  onOpen,
  thresholdDays,
}: {
  bucket: DeadlineBucket;
  documents: DocumentRow[];
  onOpen: (doc: DocumentRow) => void;
  thresholdDays: number;
}) {
  if (documents.length === 0) return null;

  return (
    <div className="flex flex-col gap-2.5">
      <div className="flex items-center gap-2">
        <span className={cn("h-1.5 w-1.5 rounded-full", BUCKET_DOT_CLASSES[bucket])} />
        <span
          className={cn(
            "text-xs font-semibold uppercase tracking-wide",
            BUCKET_LABEL_CLASSES[bucket]
          )}
        >
          {BUCKET_TITLES[bucket]} — {documents.length}
        </span>
      </div>
      <div className="divide-y divide-line rounded-lg border border-line bg-paper-raised">
        {documents.map((doc) => (
          <DeadlineRow
            key={doc.id}
            doc={doc}
            bucket={bucket}
            onOpen={onOpen}
            thresholdDays={thresholdDays}
          />
        ))}
      </div>
    </div>
  );
}

export default function TasksPage() {
  const { authFetch, user } = useAuth();
  const router = useRouter();
  const thresholdDays = user?.due_soon_threshold_days ?? DEFAULT_DUE_SOON_THRESHOLD_DAYS;
  const [documents, setDocuments] = useState<DocumentRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadDeadlines = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await authFetch("/documents/deadlines");
      if (!res.ok) throw new Error("Failed to load deadlines");
      setDocuments(await res.json());
    } catch {
      setError("Could not load deadlines from the backend.");
    } finally {
      setLoading(false);
    }
  }, [authFetch]);

  useEffect(() => {
    loadDeadlines();
  }, [loadDeadlines]);

  // "later" (beyond thresholdDays) and no-deadline documents are deliberately
  // excluded — this page is only what needs attention soon, not the full registry.
  const buckets = useMemo(() => {
    const grouped: Record<DeadlineBucket, DocumentRow[]> = {
      overdue: [],
      "due-this-week": [],
      "due-this-month": [],
      later: [],
    };
    for (const doc of documents) {
      grouped[getDeadlineBucket(doc.deadline_date, thresholdDays)].push(doc);
    }
    return grouped;
  }, [documents, thresholdDays]);

  const hasAnything = BUCKET_ORDER.some((bucket) => buckets[bucket].length > 0);

  const openDocument = useCallback(
    (doc: DocumentRow) => router.push(`/documents/${doc.id}`),
    [router]
  );

  return (
    <div className="flex flex-col gap-6 px-10 py-10">
      <div className="flex flex-col gap-1.5">
        <h1 className="font-serif text-2xl font-semibold text-ink">Tasks</h1>
        <p className="text-sm text-ink-soft">
          What needs attention, across every document in the registry.
        </p>
      </div>

      {error && <p className="text-sm text-overdue">{error}</p>}

      {loading ? (
        <p className="text-sm text-ink-soft">Loading deadlines...</p>
      ) : !hasAnything ? (
        <p className="text-sm text-ink-soft">Nothing due in the next {thresholdDays} days.</p>
      ) : (
        <div className="flex flex-col gap-6">
          {BUCKET_ORDER.map((bucket) => (
            <BucketSection
              key={bucket}
              bucket={bucket}
              documents={buckets[bucket]}
              onOpen={openDocument}
              thresholdDays={thresholdDays}
            />
          ))}
        </div>
      )}
    </div>
  );
}
