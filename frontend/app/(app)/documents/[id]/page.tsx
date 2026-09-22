"use client";

import {
  ArrowLeft,
  Download,
  ExternalLink,
  MessageSquare,
  PenLine,
  RefreshCw,
  Send,
  Trash2,
} from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { type FormEvent, type ReactNode, useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useAuth } from "@/lib/auth-context";
import { downloadSignedFile } from "@/lib/download";
import { cn } from "@/lib/utils";

import {
  CATEGORY_ICONS,
  CURRENT_CLASSIFICATION_VERSION,
  DEFAULT_DUE_SOON_THRESHOLD_DAYS,
  DocumentRow,
  FIELD_LABELS,
  FIELD_SECTIONS,
  type FieldRenderAs,
  formatDate,
  getDeadlineUrgency,
} from "../types";

// The "may be misclassified" reprocess suggestion is only useful for an "Other"
// document if it was classified before the current category list existed — a
// document deliberately (and correctly) classified as "Other" under today's full
// category list shouldn't get blanket "this might be wrong" messaging.
function isStaleOtherClassification(doc: DocumentRow): boolean {
  return (
    doc.category === "Other" && (doc.classification_version ?? 0) < CURRENT_CLASSIFICATION_VERSION
  );
}

function needsReprocessing(doc: DocumentRow): boolean {
  return (
    Boolean(doc.raw_text) && (doc.status.endsWith("_failed") || isStaleOtherClassification(doc))
  );
}

const URGENCY_DOT_CLASSES: Record<string, string> = {
  overdue: "bg-overdue",
  upcoming: "bg-due-soon",
  none: "bg-filed",
};

const URGENCY_BADGE_CLASSES: Record<string, string> = {
  overdue: "bg-overdue/10 text-overdue",
  upcoming: "bg-due-soon/10 text-due-soon",
  none: "bg-filed/10 text-filed",
};

const URGENCY_LABELS: Record<string, string> = {
  overdue: "Overdue",
  upcoming: "Due soon",
  none: "Filed",
};

function labelize(key: string): string {
  return key
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

function isNumericField(key: string): boolean {
  return /date|amount|subtotal|total/i.test(key);
}

function formatCellNumber(value: unknown): string {
  return typeof value === "number" ? value.toLocaleString() : "—";
}

interface LineItem {
  description?: string;
  hsn_sac_code?: string | null;
  quantity?: number | null;
  unit?: string | null;
  rate?: number | null;
  tax_percent?: number | null;
  amount?: number | null;
}

function LineItemsTable({ value }: { value: unknown }) {
  if (!Array.isArray(value) || value.length === 0) {
    return <p className="text-sm text-muted">No line items extracted.</p>;
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-line text-left text-xs font-medium uppercase tracking-wide text-ink-soft">
            <th className="py-2 pr-3">Description</th>
            <th className="py-2 pr-3">HSN/SAC</th>
            <th className="py-2 pr-3 text-right">Qty</th>
            <th className="py-2 pr-3">Unit</th>
            <th className="py-2 pr-3 text-right">Rate</th>
            <th className="py-2 pr-3 text-right">Tax %</th>
            <th className="py-2 text-right">Amount</th>
          </tr>
        </thead>
        <tbody>
          {value.map((raw, index) => {
            const item = raw as LineItem;
            return (
              <tr key={index} className="border-b border-line last:border-b-0">
                <td className="py-2 pr-3 text-ink">{item.description || "—"}</td>
                <td className="py-2 pr-3 text-ink-soft">{item.hsn_sac_code || "—"}</td>
                <td className="py-2 pr-3 text-right tabular-nums text-ink">
                  {formatCellNumber(item.quantity)}
                </td>
                <td className="py-2 pr-3 text-ink-soft">{item.unit || "—"}</td>
                <td className="py-2 pr-3 text-right tabular-nums text-ink">
                  {formatCellNumber(item.rate)}
                </td>
                <td className="py-2 pr-3 text-right tabular-nums text-ink-soft">
                  {typeof item.tax_percent === "number" ? `${item.tax_percent}%` : "—"}
                </td>
                <td className="py-2 text-right tabular-nums text-ink">
                  {formatCellNumber(item.amount)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

interface TaxBreakdownItem {
  label?: string;
  rate_percent?: number | null;
  amount?: number | null;
}

function TaxBreakdownTable({ value }: { value: unknown }) {
  if (!Array.isArray(value) || value.length === 0) {
    return <p className="text-sm text-muted">No tax breakdown extracted.</p>;
  }

  return (
    <table className="w-full text-sm">
      <tbody>
        {value.map((raw, index) => {
          const item = raw as TaxBreakdownItem;
          return (
            <tr key={index} className="border-b border-line last:border-b-0">
              <td className="py-1.5 pr-3 text-ink-soft">
                {item.label || "Tax"}
                {typeof item.rate_percent === "number" ? ` (${item.rate_percent}%)` : ""}
              </td>
              <td className="py-1.5 text-right tabular-nums text-ink">
                {formatCellNumber(item.amount)}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function FieldValue({
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
function FieldRow({
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

function DetailCard({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="rounded-lg border border-line bg-paper-raised px-5 py-4">
      <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-ink-soft">
        {title}
      </h3>
      {children}
    </div>
  );
}

// Renders extracted_json as one card per FIELD_SECTIONS group — e.g. an Invoice's
// "Billing details" and "Amount & tax" sections each become their own card,
// matching the design reference's stacked-card layout. `line_items` and
// `tax_breakdown` are real structured arrays from the extraction schema (Invoice/
// Purchase Order) and get dedicated table renderers instead of going through the
// generic FieldRow/FieldValue path — FieldValue's array handling renders a plain
// bulleted list of String(item), which for an array of objects would just print
// "[object Object]" per row.
function ExtractedCards({ doc }: { doc: DocumentRow }) {
  const extracted = doc.extracted_json;

  if (!extracted) {
    return (
      <DetailCard title="Extracted content">
        <div className="flex flex-col gap-4 pt-2">
          {doc.classification_reasoning && (
            <div className="flex flex-col gap-1">
              <span className="text-xs text-ink-soft">Why this category</span>
              <p className="text-sm text-ink">{doc.classification_reasoning}</p>
            </div>
          )}
          <div className="flex flex-col gap-1">
            <span className="text-xs text-ink-soft">Extracted text</span>
            {doc.raw_text ? (
              <pre className="max-h-96 overflow-y-auto whitespace-pre-wrap rounded-md bg-sidebar-bg p-3 text-sm text-ink">
                {doc.raw_text}
              </pre>
            ) : (
              <p className="text-sm text-muted">
                No text has been extracted for this document yet.
              </p>
            )}
          </div>
        </div>
      </DetailCard>
    );
  }

  if (extracted.parse_error) {
    return (
      <DetailCard title="Extracted content">
        <div className="flex flex-col gap-2 pt-2">
          <p className="rounded-md bg-due-soon/10 px-3 py-2 text-sm text-due-soon">
            Structured extraction did not return valid JSON. Showing the raw model response
            instead.
          </p>
          <pre className="whitespace-pre-wrap rounded-md bg-sidebar-bg p-3 text-xs text-ink-soft">
            {String(extracted.raw_response ?? "")}
          </pre>
        </div>
      </DetailCard>
    );
  }

  if (extracted._generated) {
    return <GeneratedDocumentCards extracted={extracted} />;
  }

  const sections = doc.category ? FIELD_SECTIONS[doc.category] : undefined;
  const labels = doc.category ? FIELD_LABELS[doc.category] : undefined;

  const renderField = (key: string, value: unknown, renderAs?: FieldRenderAs) => {
    const label = labels?.[key] ?? labelize(key);
    if (key === "line_items") {
      return (
        <div key={key} className="border-b border-line py-2.5 last:border-b-0">
          <span className="mb-2 block text-xs text-ink-soft">{label}</span>
          <LineItemsTable value={value} />
        </div>
      );
    }
    if (key === "tax_breakdown") {
      return (
        <div key={key} className="border-b border-line py-2.5 last:border-b-0">
          <span className="mb-2 block text-xs text-ink-soft">{label}</span>
          <TaxBreakdownTable value={value} />
        </div>
      );
    }
    return (
      <FieldRow key={key} label={label} value={value} renderAs={renderAs} numeric={isNumericField(key)} />
    );
  };

  if (!sections) {
    const entries = Object.entries(extracted);
    if (entries.length === 0) {
      return (
        <DetailCard title="Extracted content">
          <p className="pt-2 text-sm text-ink-soft">No structured data extracted.</p>
        </DetailCard>
      );
    }
    return (
      <DetailCard title="Extracted content">
        <div className="pt-1">{entries.map(([key, value]) => renderField(key, value))}</div>
      </DetailCard>
    );
  }

  return (
    <>
      {sections.map((section) => (
        <DetailCard key={section.title} title={section.title}>
          <div className="pt-1">
            {section.fields.map((key) =>
              renderField(key, extracted[key], section.renderAs?.[key])
            )}
          </div>
        </DetailCard>
      ))}
    </>
  );
}

// Phase 32 — see CLAUDE.md's Document generation section. A document
// produced by POST /templates/{id}/generate has extracted_json shaped as
// {_generated: true, _field_schema, _values} — a learned template's field
// keys aren't guaranteed to match structured_extraction.py's fixed schema
// names, so FIELD_SECTIONS/FIELD_LABELS (built for the fixed categories)
// don't apply here. This renders directly and generically from the
// document's own self-describing schema+values instead.
interface GeneratedField {
  key: string;
  label: string;
  type: "string" | "number" | "date";
}

interface GeneratedSection {
  id: string;
  title: string;
  type: "fields" | "table";
  fields?: GeneratedField[];
  columns?: GeneratedField[];
}

function formatGeneratedValue(value: unknown, type: string): string {
  if (value === null || value === undefined || value === "") return "—";
  if (type === "number" && typeof value === "number") {
    return value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  return String(value);
}

function GeneratedTableSection({
  section,
  rows,
}: {
  section: GeneratedSection;
  rows: Record<string, unknown>[];
}) {
  const columns = section.columns ?? [];
  if (rows.length === 0) {
    return <p className="text-sm text-muted">No rows.</p>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-line text-left text-xs font-medium uppercase tracking-wide text-ink-soft">
            {columns.map((col) => (
              <th key={col.key} className={cn("py-2 pr-3", col.type === "number" && "text-right")}>
                {col.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index} className="border-b border-line last:border-b-0">
              {columns.map((col) => (
                <td
                  key={col.key}
                  className={cn("py-2 pr-3 text-ink", col.type === "number" && "text-right tabular-nums")}
                >
                  {formatGeneratedValue(row[col.key], col.type)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function GeneratedDocumentCards({ extracted }: { extracted: Record<string, unknown> }) {
  const schema = extracted._field_schema as { sections?: GeneratedSection[] } | undefined;
  const values = (extracted._values as Record<string, unknown>) ?? {};
  const sections = schema?.sections ?? [];

  if (sections.length === 0) {
    return (
      <DetailCard title="Generated content">
        <p className="pt-2 text-sm text-ink-soft">No structured data available.</p>
      </DetailCard>
    );
  }

  return (
    <>
      {sections.map((section) => (
        <DetailCard key={section.id} title={section.title}>
          <div className="pt-1">
            {section.type === "table" ? (
              <GeneratedTableSection
                section={section}
                rows={(values[section.id] as Record<string, unknown>[]) ?? []}
              />
            ) : (
              (section.fields ?? []).map((field) => {
                const row = (values[section.id] as Record<string, unknown> | undefined) ?? {};
                return (
                  <FieldRow
                    key={field.key}
                    label={field.label}
                    value={row[field.key]}
                    numeric={field.type === "number"}
                  />
                );
              })
            )}
          </div>
        </DetailCard>
      ))}
    </>
  );
}

function ViewOriginalButton({ docId }: { docId: string }) {
  const { authFetch } = useAuth();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleClick = async () => {
    // Open the tab synchronously on the click, before the await — otherwise popup
    // blockers treat window.open() after an async gap as not user-initiated.
    // Deliberately NOT passing "noopener": it makes window.open() return null (no
    // handle back), so we sever the opener link ourselves once we have a handle.
    const newTab = window.open("", "_blank");
    if (newTab) {
      newTab.opener = null;
    }

    setLoading(true);
    setError(null);
    try {
      if (!newTab) {
        throw new Error("Your browser blocked the popup. Allow popups for this site and try again.");
      }
      const res = await authFetch(`/documents/${docId}/download-url`);
      if (!res.ok) {
        const data = await res.json().catch(() => null);
        throw new Error(data?.detail ?? "Could not open the original file.");
      }
      const data: { url: string } = await res.json();
      newTab.location.href = data.url;
    } catch (err) {
      newTab?.close();
      setError(err instanceof Error ? err.message : "Could not open the original file.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex flex-col items-end gap-1">
      <Button type="button" variant="outline" onClick={handleClick} disabled={loading}>
        <ExternalLink className="h-4 w-4" strokeWidth={1.75} />
        {loading ? "Opening..." : "View original PDF"}
      </Button>
      {error && <span className="text-xs text-overdue">{error}</span>}
    </div>
  );
}

// A real "Save to disk" download, reusing the same signed-URL endpoint as
// ViewOriginalButton above — unlike that button (which opens the file for
// viewing in a new tab), this fetches the bytes itself and saves them under
// the document's real, human-readable filename (see the filename-generation
// fix in template_generation.py/templates_router.py for generated documents;
// a regular upload's filename was already the original uploaded name).
function DownloadButton({ docId, filename }: { docId: string; filename: string }) {
  const { authFetch } = useAuth();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleClick = async () => {
    setLoading(true);
    setError(null);
    try {
      await downloadSignedFile(authFetch, `/documents/${docId}/download-url`, filename);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not download the file.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex flex-col items-end gap-1">
      <Button type="button" variant="outline" onClick={handleClick} disabled={loading}>
        <Download className="h-4 w-4" strokeWidth={1.75} />
        {loading ? "Downloading..." : "Download"}
      </Button>
      {error && <span className="text-xs text-overdue">{error}</span>}
    </div>
  );
}

interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

function ChatCard({ doc }: { doc: DocumentRow }) {
  const { authFetch } = useAuth();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [question, setQuestion] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSend = async (event: FormEvent) => {
    event.preventDefault();
    const trimmed = question.trim();
    if (!trimmed || sending) return;

    setMessages((prev) => [...prev, { role: "user", content: trimmed }]);
    setQuestion("");
    setSending(true);
    setError(null);

    try {
      const res = await authFetch(`/documents/${doc.id}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: trimmed }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => null);
        throw new Error(data?.detail ?? "Could not get an answer.");
      }
      const data: { answer: string; truncated: boolean } = await res.json();
      const content = data.truncated
        ? `${data.answer}\n\n(Note: this document is long, so only the first part of it was used to answer.)`
        : data.answer;
      setMessages((prev) => [...prev, { role: "assistant", content }]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not get an answer.");
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="flex flex-col rounded-lg border border-line bg-paper-raised lg:sticky lg:top-8">
      <div className="flex items-center gap-2 border-b border-line px-4 py-3.5">
        <MessageSquare className="h-4 w-4 text-ink" strokeWidth={1.75} />
        <span className="text-sm font-semibold text-ink">Ask about this document</span>
      </div>

      <div className="flex max-h-[50vh] flex-col gap-3 overflow-y-auto px-4 py-4">
        {messages.length === 0 ? (
          <p className="text-sm text-ink-soft">
            {doc.raw_text
              ? "Ask a question about this document's contents."
              : "This document has no extracted text yet, so there's nothing to ask about."}
          </p>
        ) : (
          messages.map((message, index) => (
            <div
              key={index}
              className={cn(
                "max-w-[90%] whitespace-pre-wrap rounded-xl px-3.5 py-2.5 text-sm leading-relaxed",
                message.role === "user"
                  ? "self-end rounded-br-sm bg-ink text-paper"
                  : "self-start rounded-bl-sm bg-sidebar-bg text-ink"
              )}
            >
              {message.content}
            </div>
          ))
        )}
        {sending && (
          <div className="self-start rounded-xl rounded-bl-sm bg-sidebar-bg px-3.5 py-2.5 text-sm text-ink-soft">
            Thinking...
          </div>
        )}
      </div>

      {error && <p className="px-4 pb-2 text-xs text-overdue">{error}</p>}

      <form onSubmit={handleSend} className="flex items-center gap-2 border-t border-line p-3.5">
        <Input
          type="text"
          placeholder="Ask a question about this document…"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          disabled={sending || !doc.raw_text}
          className="flex-1"
        />
        <Button
          type="submit"
          size="icon"
          disabled={sending || !question.trim() || !doc.raw_text}
        >
          <Send className="h-4 w-4" strokeWidth={1.75} />
        </Button>
      </form>
    </div>
  );
}

// Phase 31 — see CLAUDE.md's E-signature section. Self-serve only: this
// always signs the CURRENT user's own document with their OWN saved
// signature — there's no "send this to someone else to sign" flow here.
interface SignedVersion {
  id: string;
  document_id: string;
  filename: string;
  created_at: string;
}

function SignedDownloadButton({ signedId, filename }: { signedId: string; filename: string }) {
  const { authFetch } = useAuth();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleClick = async () => {
    // Same popup-blocker-safe pattern as ViewOriginalButton above: open the
    // tab synchronously on click, navigate it once the signed URL resolves.
    const newTab = window.open("", "_blank");
    if (newTab) newTab.opener = null;

    setLoading(true);
    setError(null);
    try {
      if (!newTab) {
        throw new Error("Your browser blocked the popup. Allow popups for this site and try again.");
      }
      const res = await authFetch(`/signed-documents/${signedId}/download-url`);
      if (!res.ok) {
        const data = await res.json().catch(() => null);
        throw new Error(data?.detail ?? "Could not open the signed file.");
      }
      const data: { url: string } = await res.json();
      newTab.location.href = data.url;
    } catch (err) {
      newTab?.close();
      setError(err instanceof Error ? err.message : "Could not open the signed file.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex flex-col items-start gap-1">
      <Button type="button" variant="outline" size="sm" onClick={handleClick} disabled={loading}>
        <Download className="h-3.5 w-3.5" strokeWidth={1.75} />
        {loading ? "Opening..." : filename}
      </Button>
      {error && <span className="text-xs text-overdue">{error}</span>}
    </div>
  );
}

// A lightweight, self-contained overlay rather than the (currently unused
// elsewhere) Dialog/@base-ui wrapper — keeps this to one simple, fully
// understood implementation for a one-off confirm step. Placement is fixed
// (bottom-right of the last page — see CLAUDE.md's E-signature section, v1
// scope), so this "preview" is a static mockup of that corner, not a real
// PDF page render — genuinely showing the actual page would need a PDF
// rendering library, out of scope for this phase.
function SignPreviewModal({
  signatureUrl,
  onCancel,
  onConfirm,
  signing,
}: {
  signatureUrl: string | null;
  onCancel: () => void;
  onConfirm: () => void;
  signing: boolean;
}) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-ink/40 backdrop-blur-sm"
      onClick={onCancel}
    >
      <div
        className="flex w-full max-w-sm flex-col gap-4 rounded-xl bg-paper-raised p-6 shadow-2xl"
        onClick={(event) => event.stopPropagation()}
      >
        <h2 className="font-serif text-lg font-semibold text-ink">Sign this document</h2>
        <p className="text-sm text-ink-soft">
          Your saved signature will be placed in the bottom-right corner of the last page, like
          this:
        </p>
        <div className="relative mx-auto aspect-[3/4] w-40 rounded-md border border-line bg-paper shadow-sm">
          {signatureUrl && (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={signatureUrl}
              alt="Signature placement preview"
              className="absolute bottom-2 right-2 h-8 w-auto max-w-[70%] object-contain"
            />
          )}
        </div>
        <div className="flex justify-end gap-2">
          <Button type="button" variant="outline" size="sm" onClick={onCancel} disabled={signing}>
            Cancel
          </Button>
          <Button type="button" size="sm" onClick={onConfirm} disabled={signing}>
            {signing ? "Signing..." : "Confirm & sign"}
          </Button>
        </div>
      </div>
    </div>
  );
}

function SignDocumentCard({ doc }: { doc: DocumentRow }) {
  const { authFetch, user } = useAuth();
  const isPdf = doc.filename.toLowerCase().endsWith(".pdf");
  const [signedVersions, setSignedVersions] = useState<SignedVersion[]>([]);
  const [loadingVersions, setLoadingVersions] = useState(isPdf);
  const [showPreview, setShowPreview] = useState(false);
  const [signatureUrl, setSignatureUrl] = useState<string | null>(null);
  const [signing, setSigning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadSignedVersions = useCallback(async () => {
    if (!isPdf) return;
    setLoadingVersions(true);
    try {
      const res = await authFetch(`/documents/${doc.id}/signed`);
      if (res.ok) setSignedVersions(await res.json());
    } catch {
      // Non-fatal — the "Sign document" action itself still works even if
      // this background fetch fails; the list just stays empty.
    } finally {
      setLoadingVersions(false);
    }
  }, [authFetch, doc.id, isPdf]);

  useEffect(() => {
    loadSignedVersions();
  }, [loadSignedVersions]);

  if (!isPdf) return null;

  const openPreview = async () => {
    setError(null);
    try {
      const res = await authFetch("/auth/signature");
      const data: { download_url: string | null } = await res.json();
      setSignatureUrl(data.download_url ?? null);
    } catch {
      setSignatureUrl(null);
    }
    setShowPreview(true);
  };

  const handleConfirmSign = async () => {
    setSigning(true);
    setError(null);
    try {
      const res = await authFetch(`/documents/${doc.id}/sign`, { method: "POST" });
      if (!res.ok) {
        const data = await res.json().catch(() => null);
        throw new Error(data?.detail ?? "Could not sign this document.");
      }
      const created: SignedVersion = await res.json();
      setSignedVersions((prev) => [created, ...prev]);
      setShowPreview(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not sign this document.");
    } finally {
      setSigning(false);
    }
  };

  return (
    <DetailCard title="Signature">
      <div className="flex flex-col gap-3 pt-2">
        {!user?.has_signature ? (
          <p className="text-sm text-ink-soft">
            Save a signature in{" "}
            <Link href="/settings" className="font-medium text-ink underline underline-offset-2">
              Settings
            </Link>{" "}
            before signing documents.
          </p>
        ) : (
          <>
            <Button type="button" variant="outline" size="sm" onClick={openPreview} className="w-fit">
              <PenLine className="h-3.5 w-3.5" strokeWidth={1.75} />
              Sign document
            </Button>
            {error && <p className="text-sm text-overdue">{error}</p>}
          </>
        )}

        {!loadingVersions && signedVersions.length > 0 && (
          <div className="flex flex-col gap-2 border-t border-line pt-3">
            <span className="text-xs text-ink-soft">
              Signed {signedVersions.length > 1 ? `${signedVersions.length} times` : "once"} — most
              recent first
            </span>
            {signedVersions.map((version) => (
              <SignedDownloadButton key={version.id} signedId={version.id} filename={version.filename} />
            ))}
          </div>
        )}
      </div>

      {showPreview && (
        <SignPreviewModal
          signatureUrl={signatureUrl}
          onCancel={() => setShowPreview(false)}
          onConfirm={handleConfirmSign}
          signing={signing}
        />
      )}
    </DetailCard>
  );
}

function ReprocessBanner({
  doc,
  onReprocessed,
}: {
  doc: DocumentRow;
  onReprocessed: (updated: DocumentRow) => void;
}) {
  const { authFetch } = useAuth();
  const [reprocessing, setReprocessing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleReprocess = async () => {
    setReprocessing(true);
    setError(null);
    try {
      const res = await authFetch(`/documents/${doc.id}/reprocess`, { method: "POST" });
      if (!res.ok) {
        const data = await res.json().catch(() => null);
        throw new Error(data?.detail ?? "Reprocessing failed.");
      }
      const updated: DocumentRow = await res.json();
      onReprocessed(updated);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Reprocessing failed.");
    } finally {
      setReprocessing(false);
    }
  };

  return (
    <div className="flex items-center justify-between gap-3 rounded-md bg-due-soon/10 px-3 py-2">
      <div className="text-sm text-due-soon">
        {error ??
          (doc.status.endsWith("_failed")
            ? "This document failed processing. It can be safely retried without re-uploading."
            : "This was classified before some newer document categories existed — retry to reclassify against the full list.")}
      </div>
      <Button
        type="button"
        size="sm"
        variant="outline"
        onClick={handleReprocess}
        disabled={reprocessing}
        className="shrink-0"
      >
        <RefreshCw className={cn("h-3.5 w-3.5", reprocessing && "animate-spin")} strokeWidth={1.75} />
        {reprocessing ? "Reprocessing..." : "Reprocess"}
      </Button>
    </div>
  );
}

export default function DocumentDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const { authFetch, user } = useAuth();
  const thresholdDays = user?.due_soon_threshold_days ?? DEFAULT_DUE_SOON_THRESHOLD_DAYS;
  const [doc, setDoc] = useState<DocumentRow | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const res = await authFetch(`/documents/${params.id}`);
      if (res.status === 404) {
        setLoadError("This document could not be found — it may have been deleted.");
        setDoc(null);
        return;
      }
      if (!res.ok) throw new Error("Failed to load document");
      setDoc(await res.json());
    } catch {
      setLoadError("Could not load this document.");
    } finally {
      setLoading(false);
    }
  }, [authFetch, params.id]);

  useEffect(() => {
    load();
  }, [load]);

  const handleDelete = async () => {
    if (!doc) return;
    if (!window.confirm(`Delete "${doc.filename}"? This can't be undone.`)) return;

    setDeleting(true);
    setDeleteError(null);
    try {
      const res = await authFetch(`/documents/${doc.id}`, { method: "DELETE" });
      if (!res.ok) throw new Error("Delete failed");
      router.push("/documents");
    } catch {
      setDeleteError("Could not delete the document. Please try again.");
      setDeleting(false);
    }
  };

  const BackLink = (
    <Link
      href="/documents"
      className="flex w-fit items-center gap-1.5 text-sm font-medium text-ink-soft hover:text-ink"
    >
      <ArrowLeft className="h-3.5 w-3.5" strokeWidth={2} />
      Documents
    </Link>
  );

  if (loading) {
    return (
      <div className="flex flex-col gap-6 px-10 py-8">
        {BackLink}
        <p className="text-sm text-ink-soft">Loading document...</p>
      </div>
    );
  }

  if (!doc) {
    return (
      <div className="flex flex-col gap-6 px-10 py-8">
        {BackLink}
        <p className="text-sm text-overdue">
          {loadError ?? "This document could not be found."}
        </p>
      </div>
    );
  }

  const urgency = getDeadlineUrgency(doc.deadline_date, thresholdDays);
  const CategoryIcon = doc.category ? CATEGORY_ICONS[doc.category] : CATEGORY_ICONS.Other;

  return (
    <div className="flex flex-col gap-6 px-10 py-8">
      {BackLink}

      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex flex-col gap-2.5">
          <h1 className="font-serif text-2xl font-medium text-ink">{doc.filename}</h1>
          <div className="flex flex-wrap items-center gap-2.5">
            <span className="inline-flex items-center gap-1.5 rounded-md bg-sidebar-bg px-2.5 py-1 text-xs font-semibold text-ink-soft">
              <CategoryIcon className="h-3.5 w-3.5" strokeWidth={1.75} />
              {doc.category ?? "Uncategorized"}
            </span>
            <span
              className={cn(
                "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold",
                URGENCY_BADGE_CLASSES[urgency]
              )}
            >
              <span className={cn("h-1.5 w-1.5 rounded-full", URGENCY_DOT_CLASSES[urgency])} />
              {URGENCY_LABELS[urgency]}
            </span>
            <span className="text-xs text-ink-soft">Uploaded {formatDate(doc.upload_date)}</span>
          </div>
        </div>
        <div className="flex shrink-0 items-start gap-2">
          <ViewOriginalButton docId={doc.id} />
          <DownloadButton docId={doc.id} filename={doc.filename} />
          <div className="flex flex-col items-end gap-1">
            <Button type="button" variant="outline" onClick={handleDelete} disabled={deleting}>
              <Trash2 className="h-4 w-4" strokeWidth={1.75} />
              {deleting ? "Deleting..." : "Delete"}
            </Button>
            {deleteError && <span className="text-xs text-overdue">{deleteError}</span>}
          </div>
        </div>
      </div>

      {needsReprocessing(doc) && <ReprocessBanner doc={doc} onReprocessed={setDoc} />}
      {doc.error_message && (
        <p className="rounded-md bg-overdue/10 px-3 py-2 text-sm text-overdue">
          {doc.error_message}
        </p>
      )}

      <div className="flex flex-col gap-5 lg:flex-row">
        <div className="flex flex-1 flex-col gap-5">
          <ExtractedCards doc={doc} />
          <SignDocumentCard doc={doc} />
        </div>
        <div className="w-full lg:w-[360px] lg:shrink-0">
          <ChatCard doc={doc} />
        </div>
      </div>
    </div>
  );
}
