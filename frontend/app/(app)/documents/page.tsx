"use client";

import { Search, Upload as UploadIcon } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { type MouseEvent, useCallback, useEffect, useMemo, useState } from "react";

import { buttonVariants } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { useAuth } from "@/lib/auth-context";
import { cn } from "@/lib/utils";

import { DocumentsTable } from "./DocumentsTable";
import {
  CATEGORIES,
  DEFAULT_DUE_SOON_THRESHOLD_DAYS,
  DocumentRow,
  getDeadlineUrgency,
  sortByDeadlineAscending,
} from "./types";

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
      <span className={cn("font-serif text-3xl font-semibold tabular-nums", valueClass)}>
        {count}
      </span>
    </Card>
  );
}

export default function DocumentsPage() {
  const { authFetch, user } = useAuth();
  const router = useRouter();
  const thresholdDays = user?.due_soon_threshold_days ?? DEFAULT_DUE_SOON_THRESHOLD_DAYS;
  const [documents, setDocuments] = useState<DocumentRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [category, setCategory] = useState("");
  const [uploadDateFrom, setUploadDateFrom] = useState("");
  const [uploadDateTo, setUploadDateTo] = useState("");
  const [search, setSearch] = useState("");
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const loadDocuments = useCallback(async () => {
    setLoading(true);
    setError(null);

    const params = new URLSearchParams();
    if (category) params.set("category", category);
    if (uploadDateFrom) params.set("upload_date_from", uploadDateFrom);
    if (uploadDateTo) params.set("upload_date_to", uploadDateTo);

    try {
      const res = await authFetch(`/documents?${params.toString()}`);
      if (!res.ok) throw new Error("Failed to load documents");
      const data: DocumentRow[] = await res.json();
      setDocuments(sortByDeadlineAscending(data));
    } catch {
      setError("Could not load documents from the backend.");
    } finally {
      setLoading(false);
    }
  }, [authFetch, category, uploadDateFrom, uploadDateTo]);

  useEffect(() => {
    loadDocuments();
  }, [loadDocuments]);

  const stats = useMemo(() => {
    let overdue = 0;
    let dueSoon = 0;
    let filed = 0;
    for (const doc of documents) {
      const urgency = getDeadlineUrgency(doc.deadline_date, thresholdDays);
      if (urgency === "overdue") overdue += 1;
      else if (urgency === "upcoming") dueSoon += 1;
      else filed += 1;
    }
    return { overdue, dueSoon, filed };
  }, [documents, thresholdDays]);

  const visibleDocuments = useMemo(() => {
    if (!search.trim()) return documents;
    const term = search.trim().toLowerCase();
    return documents.filter((doc) => doc.filename.toLowerCase().includes(term));
  }, [documents, search]);

  const hasFilters = Boolean(category || uploadDateFrom || uploadDateTo || search);

  const handleDelete = useCallback(
    async (doc: DocumentRow, event: MouseEvent) => {
      event.stopPropagation();
      if (!window.confirm(`Delete "${doc.filename}"? This can't be undone.`)) return;

      setDeletingId(doc.id);
      try {
        const res = await authFetch(`/documents/${doc.id}`, { method: "DELETE" });
        if (!res.ok) throw new Error("Delete failed");
        setDocuments((prev) => prev.filter((d) => d.id !== doc.id));
      } catch {
        setError("Could not delete the document. Please try again.");
      } finally {
        setDeletingId(null);
      }
    },
    [authFetch]
  );

  return (
    <div className="flex flex-col">
      <div className="relative overflow-hidden">
        <div
          className="pointer-events-none absolute inset-0"
          style={{
            backgroundImage:
              "radial-gradient(circle at 100% 0%, rgba(63,102,89,0.12) 0%, transparent 60%), radial-gradient(circle, rgba(27,31,59,0.06) 1px, transparent 1px)",
            backgroundSize: "auto, 16px 16px",
          }}
        />
        <div className="relative flex items-start justify-between gap-4 px-10 py-10">
          <div>
            <h1 className="font-serif text-2xl font-semibold text-ink">Documents</h1>
            <p className="mt-1 text-sm text-ink-soft">
              Every contract, invoice, and filing your team has on file
            </p>
          </div>
          <Link href="/upload" className={buttonVariants({ variant: "default" })}>
            <UploadIcon className="h-4 w-4" strokeWidth={1.75} />
            Upload document
          </Link>
        </div>
      </div>

      <div className="flex flex-col gap-6 px-10 pb-10">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <StatTile
            label="Overdue"
            count={stats.overdue}
            dotClass="bg-overdue"
            valueClass="text-overdue"
          />
          <StatTile
            label={`Due within ${thresholdDays} days`}
            count={stats.dueSoon}
            dotClass="bg-due-soon"
            valueClass="text-due-soon"
          />
          <StatTile label="Filed" count={stats.filed} dotClass="bg-filed" valueClass="text-filed" />
        </div>

        <div className="flex flex-wrap items-end gap-3">
          <div className="flex flex-col gap-1">
            <label className="text-xs font-medium text-ink-soft">Category</label>
            <select
              value={category}
              onChange={(event) => setCategory(event.target.value)}
              className="h-10 rounded-md border border-line bg-paper-raised px-3 text-sm text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ink/20"
            >
              <option value="">All categories</option>
              {CATEGORIES.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </div>

          <div className="flex flex-col gap-1">
            <label className="text-xs font-medium text-ink-soft">Uploaded from</label>
            <Input
              type="date"
              value={uploadDateFrom}
              onChange={(event) => setUploadDateFrom(event.target.value)}
              className="w-40"
            />
          </div>

          <div className="flex flex-col gap-1">
            <label className="text-xs font-medium text-ink-soft">Uploaded to</label>
            <Input
              type="date"
              value={uploadDateTo}
              onChange={(event) => setUploadDateTo(event.target.value)}
              className="w-40"
            />
          </div>

          <div className="flex flex-col gap-1">
            <label className="text-xs font-medium text-ink-soft">Search</label>
            <div className="relative">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted" />
              <Input
                type="text"
                placeholder="Search by filename"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                className="w-56 pl-9"
              />
            </div>
          </div>

          {hasFilters && (
            <button
              onClick={() => {
                setCategory("");
                setUploadDateFrom("");
                setUploadDateTo("");
                setSearch("");
              }}
              className="h-10 rounded-md px-3 text-sm text-ink-soft hover:bg-sidebar-bg"
            >
              Clear filters
            </button>
          )}
        </div>

        {error && <p className="text-sm text-overdue">{error}</p>}

        {loading ? (
          <p className="text-sm text-ink-soft">Loading documents...</p>
        ) : (
          <DocumentsTable
            documents={visibleDocuments}
            onRowClick={(doc) => router.push(`/documents/${doc.id}`)}
            onDelete={handleDelete}
            deletingId={deletingId}
            thresholdDays={thresholdDays}
          />
        )}
      </div>
    </div>
  );
}
