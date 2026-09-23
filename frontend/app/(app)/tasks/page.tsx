"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  BUCKET_DOT_CLASSES,
  BUCKET_LABEL_CLASSES,
  BUCKET_TITLES,
  DeadlineRow,
} from "@/components/deadline-row";
import { useAuth } from "@/lib/auth-context";
import { cn } from "@/lib/utils";

import {
  type DeadlineBucket,
  DEFAULT_DUE_SOON_THRESHOLD_DAYS,
  DocumentRow,
  getDeadlineBucket,
} from "../documents/types";

const BUCKET_ORDER: DeadlineBucket[] = ["overdue", "due-this-week", "due-this-month"];

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
