"use client";

import { Download, Trash2 } from "lucide-react";
import type { MouseEvent } from "react";
import { useState } from "react";

import { useAuth } from "@/lib/auth-context";
import { downloadSignedFile } from "@/lib/download";
import { cn } from "@/lib/utils";

import {
  DEFAULT_DUE_SOON_THRESHOLD_DAYS,
  DocumentRow,
  formatDate,
  formatRelativeDeadline,
  getDeadlineUrgency,
} from "./types";

// Self-contained (calls useAuth() itself, same pattern as
// ViewOriginalButton/DownloadButton on the document detail page) so this
// table doesn't need a new prop threaded down from the parent page just for
// this one action.
function DownloadRowButton({ doc }: { doc: DocumentRow }) {
  const { authFetch } = useAuth();
  const [downloading, setDownloading] = useState(false);

  const handleDownload = async (event: MouseEvent) => {
    event.stopPropagation();
    if (downloading) return;
    setDownloading(true);
    try {
      await downloadSignedFile(authFetch, `/documents/${doc.id}/download-url`, doc.filename);
    } catch (err) {
      window.alert(err instanceof Error ? err.message : "Could not download the file.");
    } finally {
      setDownloading(false);
    }
  };

  return (
    <button
      type="button"
      onClick={handleDownload}
      disabled={downloading}
      aria-label={`Download ${doc.filename}`}
      className="rounded-md p-1.5 text-ink-soft hover:bg-sidebar-bg hover:text-ink disabled:opacity-50"
    >
      <Download className="h-4 w-4" strokeWidth={1.75} />
    </button>
  );
}

const URGENCY_TEXT_CLASSES: Record<string, string> = {
  overdue: "text-overdue",
  upcoming: "text-due-soon",
  none: "text-ink-soft",
};

function statusDotClass(status: string): string {
  if (status.endsWith("_failed")) return "bg-overdue";
  if (status === "processed") return "bg-filed";
  return "bg-due-soon";
}

export function DocumentsTable({
  documents,
  onRowClick,
  onDelete,
  deletingId,
  emptyMessage = "No documents found.",
  thresholdDays = DEFAULT_DUE_SOON_THRESHOLD_DAYS,
}: {
  documents: DocumentRow[];
  onRowClick: (doc: DocumentRow) => void;
  onDelete: (doc: DocumentRow, event: MouseEvent) => void;
  deletingId: string | null;
  emptyMessage?: string;
  thresholdDays?: number;
}) {
  if (documents.length === 0) {
    return <p className="text-sm text-ink-soft">{emptyMessage}</p>;
  }

  return (
    <div className="overflow-x-auto rounded-lg border border-line bg-paper-raised">
      <table className="min-w-full divide-y divide-line text-sm">
        <thead>
          <tr className="text-left text-xs font-medium uppercase tracking-wide text-ink-soft">
            <th className="px-4 py-3">Document</th>
            <th className="px-4 py-3">Category</th>
            <th className="px-4 py-3">Uploaded</th>
            <th className="px-4 py-3">Deadline</th>
            <th className="px-4 py-3">Status</th>
            <th className="px-4 py-3" />
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {documents.map((doc) => {
            const urgency = getDeadlineUrgency(doc.deadline_date, thresholdDays);
            const relative = formatRelativeDeadline(doc.deadline_date, thresholdDays);
            return (
              <tr
                key={doc.id}
                onClick={() => onRowClick(doc)}
                className={cn(
                  "cursor-pointer hover:bg-sidebar-bg/50",
                  urgency === "overdue" && "border-l-2 border-overdue"
                )}
              >
                <td className="px-4 py-3 text-ink">{doc.filename}</td>
                <td className="px-4 py-3 text-ink-soft">{doc.category ?? "—"}</td>
                <td className="px-4 py-3 tabular-nums text-ink-soft">
                  {formatDate(doc.upload_date)}
                </td>
                <td className="px-4 py-3">
                  <div className="flex flex-col">
                    <span className="tabular-nums text-ink">{formatDate(doc.deadline_date)}</span>
                    {relative && (
                      <span className={cn("text-xs", URGENCY_TEXT_CLASSES[urgency])}>
                        {relative}
                      </span>
                    )}
                  </div>
                </td>
                <td className="px-4 py-3">
                  <span className="flex items-center gap-2 text-ink-soft">
                    <span className={cn("h-1.5 w-1.5 rounded-full", statusDotClass(doc.status))} />
                    {doc.status}
                  </span>
                </td>
                <td className="px-4 py-3 text-right">
                  <div className="flex items-center justify-end gap-1">
                    <DownloadRowButton doc={doc} />
                    <button
                      type="button"
                      onClick={(event) => onDelete(doc, event)}
                      disabled={deletingId === doc.id}
                      aria-label={`Delete ${doc.filename}`}
                      className="rounded-md p-1.5 text-ink-soft hover:bg-overdue/10 hover:text-overdue disabled:opacity-50"
                    >
                      <Trash2 className="h-4 w-4" strokeWidth={1.75} />
                    </button>
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
