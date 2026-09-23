import { ChevronRight } from "lucide-react";

import { cn } from "@/lib/utils";

import {
  type DeadlineBucket,
  DocumentRow,
  formatDate,
  formatRelativeDeadline,
  getPartySummary,
} from "@/app/(app)/documents/types";

// Extracted out of tasks/page.tsx so the landing page's "Track deadlines"
// preview (see CLAUDE.md's Public landing page section) can reuse the exact
// same row component with sample data, instead of a separate visual
// re-creation of it. "later" is a real bucket value (see DeadlineBucket in
// documents/types.ts) that the real /tasks page computes but never renders
// (BUCKET_ORDER there deliberately excludes it — a document that isn't due
// soon doesn't belong on a "what needs attention" page), so its color here
// (filed/green, the same "on track" token used everywhere else in this app)
// has no visible effect on /tasks itself — it only becomes visible via this
// shared component, in the landing page's preview.
export const BUCKET_TITLES: Record<DeadlineBucket, string> = {
  overdue: "Overdue",
  "due-this-week": "Due this week",
  "due-this-month": "Due soon",
  later: "On track",
};

export const BUCKET_DOT_CLASSES: Record<DeadlineBucket, string> = {
  overdue: "bg-overdue",
  "due-this-week": "bg-due-soon",
  "due-this-month": "bg-ink-soft",
  later: "bg-filed",
};

export const BUCKET_LABEL_CLASSES: Record<DeadlineBucket, string> = {
  overdue: "text-overdue",
  "due-this-week": "text-due-soon",
  "due-this-month": "text-ink-soft",
  later: "text-filed",
};

export const BUCKET_PILL_CLASSES: Record<DeadlineBucket, string> = {
  overdue: "bg-overdue/10 text-overdue",
  "due-this-week": "bg-due-soon/10 text-due-soon",
  "due-this-month": "bg-sidebar-bg text-ink-soft",
  later: "bg-filed/10 text-filed",
};

export function DeadlineRow({
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
      className="flex w-full items-center gap-4 px-5 py-3.5 text-left transition-colors hover:bg-sidebar-bg/40 max-sm:gap-2 max-sm:px-3"
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
      {/* max-sm:w-auto: below the sm breakpoint the date column above is
          already hidden, so the fixed w-32 here just steals room the
          filename badly needs on a narrow screen (see the landing page's
          "Track deadlines" preview, which is the first place this component
          has ever needed to render below sm width) — collapsing to content
          width there fixes that with zero visual change at sm and up, which
          is the only width /tasks itself is ever realistically viewed at. */}
      <span className="flex w-32 shrink-0 justify-end max-sm:w-auto">
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
