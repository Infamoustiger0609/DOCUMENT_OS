import { type ReactNode } from "react";

import { cn } from "@/lib/utils";

import { type FieldRenderAs } from "@/app/(app)/documents/types";

// Extracted out of documents/[id]/page.tsx so the landing page's "Extract"
// preview (see CLAUDE.md's Public landing page section) can render a real
// sample extracted-fields card using the exact same components the document
// detail page uses — not a visual re-creation of it.

export function FieldValue({
  value,
  renderAs,
  numeric,
}: {
  value: unknown;
  renderAs?: FieldRenderAs;
  numeric: boolean;
}) {
  if (value === null || value === undefined || value === "") {
    return <span className="text-sm text-muted">—</span>;
  }

  if (renderAs === "boolean" || typeof value === "boolean") {
    return <span className="text-sm text-ink">{value ? "Yes" : "No"}</span>;
  }

  if (renderAs === "list" || Array.isArray(value)) {
    const items = Array.isArray(value) ? value : [value];
    if (items.length === 0) return <span className="text-sm text-muted">—</span>;
    return (
      <ul className="list-disc space-y-1 pl-4 text-sm text-ink">
        {items.map((item, index) => (
          <li key={index}>{String(item)}</li>
        ))}
      </ul>
    );
  }

  if (renderAs === "prose") {
    return <p className="text-sm leading-relaxed text-ink">{String(value)}</p>;
  }

  return (
    <span className={cn("text-sm text-ink", numeric && "tabular-nums")}>{String(value)}</span>
  );
}

// Label-left/value-right single-line row (design-reference layout pattern), except
// list/prose fields which need more room and stack label-above-value instead.
export function FieldRow({
  label,
  value,
  renderAs,
  numeric,
}: {
  label: string;
  value: unknown;
  renderAs?: FieldRenderAs;
  numeric: boolean;
}) {
  const isBlock = renderAs === "list" || renderAs === "prose";

  if (isBlock) {
    return (
      <div className="flex flex-col gap-1.5 border-b border-line py-2.5 last:border-b-0">
        <span className="text-xs text-ink-soft">{label}</span>
        <FieldValue value={value} renderAs={renderAs} numeric={numeric} />
      </div>
    );
  }

  return (
    <div className="flex items-center justify-between gap-4 border-b border-line py-2.5 last:border-b-0">
      <span className="shrink-0 text-sm text-ink-soft">{label}</span>
      <span className="text-right">
        <FieldValue value={value} renderAs={renderAs} numeric={numeric} />
      </span>
    </div>
  );
}

export function DetailCard({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="rounded-lg border border-line bg-paper-raised px-5 py-4">
      <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-ink-soft">
        {title}
      </h3>
      {children}
    </div>
  );
}
