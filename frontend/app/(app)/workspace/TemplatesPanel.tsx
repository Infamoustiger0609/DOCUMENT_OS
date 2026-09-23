"use client";

// Phase 32 — see CLAUDE.md's Document generation section. Deliberately a
// separate component/file from the rest of workspace/page.tsx's tool picker:
// this is a genuinely distinct workflow (learn a field structure once, reuse
// it to generate brand-new documents), not another /tools/* transformation.
import { Download, Eye, FileText, Loader2, Plus, Sparkles, Trash2, UploadCloud, X } from "lucide-react";
import Link from "next/link";
import { type ChangeEvent, type FormEvent, useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useAuth } from "@/lib/auth-context";
import { downloadSignedFile } from "@/lib/download";
import { cn } from "@/lib/utils";

import { formatDate } from "../documents/types";

type FieldType = "string" | "number" | "date";

interface SchemaField {
  key: string;
  label: string;
  type: FieldType;
}

interface SchemaSection {
  id: string;
  title: string;
  type: "fields" | "table";
  fields?: SchemaField[];
  columns?: SchemaField[];
}

interface FieldSchema {
  document_type: string;
  sections: SchemaSection[];
}

interface TemplateSummary {
  id: string;
  name: string;
  category: string;
  field_schema: FieldSchema;
  created_from_document_id: string | null;
  created_at: string;
}

type Row = Record<string, string>;
// Keyed by section id — a "fields" section's value is one Row, a "table"
// section's value is an array of Row (one per line item).
type FormValues = Record<string, Row | Row[]>;

function buildEmptyRow(fields: SchemaField[]): Row {
  const row: Row = {};
  for (const field of fields) row[field.key] = "";
  return row;
}

function buildEmptyValues(schema: FieldSchema): FormValues {
  const values: FormValues = {};
  for (const section of schema.sections) {
    values[section.id] =
      section.type === "table" ? [buildEmptyRow(section.columns ?? [])] : buildEmptyRow(section.fields ?? []);
  }
  return values;
}

function coerceField(raw: string | undefined, type: FieldType): string | number | null {
  if (!raw || !raw.trim()) return null;
  if (type === "number") {
    const n = Number(raw);
    return Number.isNaN(n) ? null : n;
  }
  return raw;
}

function coerceValuesForSubmit(schema: FieldSchema, values: FormValues): Record<string, unknown> {
  const result: Record<string, unknown> = {};
  for (const section of schema.sections) {
    if (section.type === "table") {
      const rows = (values[section.id] as Row[]) ?? [];
      const columns = section.columns ?? [];
      result[section.id] = rows
        .map((row) => {
          const coerced: Record<string, unknown> = {};
          for (const col of columns) coerced[col.key] = coerceField(row[col.key], col.type);
          return coerced;
        })
        .filter((row) => Object.values(row).some((v) => v !== null));
    } else {
      const row = (values[section.id] as Row) ?? {};
      const fields = section.fields ?? [];
      const coerced: Record<string, unknown> = {};
      for (const field of fields) coerced[field.key] = coerceField(row[field.key], field.type);
      result[section.id] = coerced;
    }
  }
  return result;
}

function inputTypeFor(fieldType: FieldType): string {
  if (fieldType === "number") return "number";
  if (fieldType === "date") return "date";
  return "text";
}

// --- Create-template card ---------------------------------------------------

function CreateTemplateCard({ onCreated }: { onCreated: (template: TemplateSummary) => void }) {
  const { authFetch } = useAuth();
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (!file || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const formData = new FormData();
      formData.append("file", file, file.name);
      if (name.trim()) formData.append("name", name.trim());
      const res = await authFetch("/templates/learn-from-sample", { method: "POST", body: formData });
      if (!res.ok) {
        const data = await res.json().catch(() => null);
        throw new Error(data?.detail ?? "Could not learn a template from this sample.");
      }
      const template: TemplateSummary = await res.json();
      onCreated(template);
      setFile(null);
      setName("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not learn a template from this sample.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Card className="flex flex-col gap-4 p-5">
      <div>
        <h2 className="text-sm font-semibold text-ink">Learn a template from a sample</h2>
        <p className="mt-0.5 text-xs text-ink-soft">
          Upload a sample Invoice or Agreement — its field structure (header fields, line-item
          columns, section order) is learned, never the actual values in your sample.
        </p>
      </div>
      <form onSubmit={handleSubmit} className="flex flex-col gap-3">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="template-name">Template name (optional)</Label>
          <Input
            id="template-name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="e.g. Standard Invoice"
          />
        </div>
        <label className="flex cursor-pointer flex-col items-center gap-2 rounded-md border border-dashed border-line bg-paper px-4 py-6 text-center hover:bg-sidebar-bg">
          <UploadCloud className="h-6 w-6 text-ink-soft" strokeWidth={1.5} />
          <span className="text-sm text-ink">{file ? file.name : "Choose a sample PDF, JPG, PNG, or TIFF"}</span>
          <input
            type="file"
            accept=".pdf,.jpg,.jpeg,.png,.tif,.tiff"
            className="hidden"
            onChange={(event: ChangeEvent<HTMLInputElement>) => setFile(event.target.files?.[0] ?? null)}
          />
        </label>
        {error && <p className="text-sm text-overdue">{error}</p>}
        <Button type="submit" disabled={!file || submitting} className="w-fit">
          {submitting ? "Learning template..." : "Learn template"}
        </Button>
      </form>
    </Card>
  );
}

// --- Generate-from-template panel ------------------------------------------

function TableSectionEditor({
  section,
  rows,
  onChange,
}: {
  section: SchemaSection;
  rows: Row[];
  onChange: (rows: Row[]) => void;
}) {
  const columns = section.columns ?? [];

  const updateCell = (rowIndex: number, key: string, value: string) => {
    onChange(rows.map((row, i) => (i === rowIndex ? { ...row, [key]: value } : row)));
  };

  return (
    <div className="flex flex-col gap-2">
      <span className="text-xs font-medium text-ink-soft">{section.title}</span>
      <div className="overflow-x-auto rounded-md border border-line">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-line bg-paper text-left text-xs uppercase tracking-wide text-ink-soft">
              {columns.map((col) => (
                <th key={col.key} className="px-2 py-2">
                  {col.label}
                </th>
              ))}
              <th className="w-8" />
            </tr>
          </thead>
          <tbody>
            {rows.map((row, rowIndex) => (
              <tr key={rowIndex} className="border-b border-line last:border-b-0">
                {columns.map((col) => (
                  <td key={col.key} className="px-2 py-1.5">
                    <Input
                      type={inputTypeFor(col.type)}
                      value={row[col.key] ?? ""}
                      onChange={(event) => updateCell(rowIndex, col.key, event.target.value)}
                      className="h-8 text-sm"
                    />
                  </td>
                ))}
                <td className="px-1">
                  <button
                    type="button"
                    onClick={() => onChange(rows.filter((_, i) => i !== rowIndex))}
                    disabled={rows.length === 1}
                    aria-label="Remove row"
                    className="rounded p-1 text-ink-soft hover:bg-sidebar-bg hover:text-overdue disabled:opacity-30"
                  >
                    <X className="h-3.5 w-3.5" strokeWidth={1.75} />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={() => onChange([...rows, buildEmptyRow(columns)])}
        className="w-fit"
      >
        <Plus className="h-3.5 w-3.5" strokeWidth={1.75} />
        Add row
      </Button>
    </div>
  );
}

function FieldsSectionEditor({
  section,
  row,
  onChange,
}: {
  section: SchemaSection;
  row: Row;
  onChange: (row: Row) => void;
}) {
  const fields = section.fields ?? [];
  return (
    <div className="flex flex-col gap-2">
      <span className="text-xs font-medium text-ink-soft">{section.title}</span>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {fields.map((field) => (
          <div key={field.key} className="flex flex-col gap-1">
            <Label htmlFor={`${section.id}-${field.key}`}>{field.label}</Label>
            <Input
              id={`${section.id}-${field.key}`}
              type={inputTypeFor(field.type)}
              value={row[field.key] ?? ""}
              onChange={(event) => onChange({ ...row, [field.key]: event.target.value })}
            />
          </div>
        ))}
      </div>
    </div>
  );
}

function GeneratePanel({
  template,
  onClose,
  onGenerated,
}: {
  template: TemplateSummary;
  onClose: () => void;
  onGenerated: () => void;
}) {
  const { authFetch } = useAuth();
  const [mode, setMode] = useState<"form" | "describe">("form");
  const [values, setValues] = useState<FormValues>(() => buildEmptyValues(template.field_schema));
  const [instruction, setInstruction] = useState("");
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [generatedDocId, setGeneratedDocId] = useState<string | null>(null);

  const handleGenerate = async () => {
    setGenerating(true);
    setError(null);
    setGeneratedDocId(null);
    try {
      const body =
        mode === "form"
          ? { values: coerceValuesForSubmit(template.field_schema, values) }
          : { instruction: instruction.trim() };
      if (mode === "describe" && !instruction.trim()) {
        throw new Error("Describe what you'd like generated first.");
      }
      const res = await authFetch(`/templates/${template.id}/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => null);
        throw new Error(data?.detail ?? "Could not generate a document.");
      }
      const data: { id: string } = await res.json();
      setGeneratedDocId(data.id);
      onGenerated();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not generate a document.");
    } finally {
      setGenerating(false);
    }
  };

  return (
    <Card className="flex flex-col gap-4 p-5">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-ink">Generate from &ldquo;{template.name}&rdquo;</h3>
        <button type="button" onClick={onClose} className="rounded p-1 text-ink-soft hover:bg-sidebar-bg">
          <X className="h-4 w-4" strokeWidth={1.75} />
        </button>
      </div>

      <div className="flex w-fit gap-1 rounded-md border border-line bg-paper p-1">
        {(["form", "describe"] as const).map((m) => (
          <button
            key={m}
            type="button"
            onClick={() => setMode(m)}
            className={cn(
              "rounded px-3 py-1.5 text-sm transition-colors",
              mode === m ? "bg-ink text-paper" : "text-ink-soft hover:bg-sidebar-bg"
            )}
          >
            {m === "form" ? "Fill a form" : "Describe it"}
          </button>
        ))}
      </div>

      {mode === "form" ? (
        <div className="flex flex-col gap-4">
          {template.field_schema.sections.map((section) =>
            section.type === "table" ? (
              <TableSectionEditor
                key={section.id}
                section={section}
                rows={(values[section.id] as Row[]) ?? []}
                onChange={(rows) => setValues((prev) => ({ ...prev, [section.id]: rows }))}
              />
            ) : (
              <FieldsSectionEditor
                key={section.id}
                section={section}
                row={(values[section.id] as Row) ?? {}}
                onChange={(row) => setValues((prev) => ({ ...prev, [section.id]: row }))}
              />
            )
          )}
        </div>
      ) : (
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="generate-instruction">Describe what you&apos;d like generated</Label>
          <textarea
            id="generate-instruction"
            value={instruction}
            onChange={(event) => setInstruction(event.target.value)}
            placeholder='e.g. "Generate an invoice for Northwind Traders, 10 hours of consulting at $150/hr, due in 30 days."'
            rows={4}
            className="w-full rounded-md border border-line bg-paper-raised px-3 py-2 text-sm text-ink placeholder:text-muted focus:outline-none focus:ring-2 focus:ring-ink/20"
          />
        </div>
      )}

      {error && <p className="text-sm text-overdue">{error}</p>}

      {generatedDocId ? (
        <div className="flex items-center justify-between gap-3 rounded-md bg-filed/10 px-3 py-2.5">
          <span className="text-sm text-filed">Document generated.</span>
          <Link
            href={`/documents/${generatedDocId}?from=workspace`}
            className="text-sm font-medium text-ink underline underline-offset-2"
          >
            View document
          </Link>
        </div>
      ) : (
        <Button type="button" onClick={handleGenerate} disabled={generating} className="w-fit">
          {generating && <Loader2 className="h-4 w-4 animate-spin" strokeWidth={1.75} />}
          {generating ? "Generating..." : "Generate document"}
        </Button>
      )}
    </Card>
  );
}

// --- Uploaded Templates / Generated Documents sections ----------------------
// Both are ordinary `documents` rows under the hood (a template-learning
// sample from POST /templates/learn-from-sample, or a template-generated
// output from POST /templates/{id}/generate) — see CLAUDE.md's "Document
// source separation" section for why they're marked with a `source` field
// and kept out of the main Documents list, surfacing only here instead.

interface SourceDocument {
  id: string;
  filename: string;
  upload_date: string;
}

function OpenDocumentButton({ id, filename }: { id: string; filename: string }) {
  return (
    <Link
      href={`/documents/${id}?from=workspace`}
      title={`Open ${filename}`}
      aria-label={`Open ${filename}`}
      className="rounded-md p-1.5 text-ink-soft hover:bg-sidebar-bg hover:text-ink"
    >
      <Eye className="h-3.5 w-3.5" strokeWidth={1.75} />
    </Link>
  );
}

function DownloadDocumentButton({ id, filename }: { id: string; filename: string }) {
  const { authFetch } = useAuth();
  const [downloading, setDownloading] = useState(false);

  const handleDownload = async () => {
    if (downloading) return;
    setDownloading(true);
    try {
      await downloadSignedFile(authFetch, `/documents/${id}/download-url`, filename);
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
      title={`Download ${filename}`}
      aria-label={`Download ${filename}`}
      className="rounded-md p-1.5 text-ink-soft hover:bg-sidebar-bg hover:text-ink disabled:opacity-50"
    >
      <Download className="h-3.5 w-3.5" strokeWidth={1.75} />
    </button>
  );
}

function SourceDocumentsCard({
  title,
  emptyMessage,
  endpoint,
  refreshKey,
}: {
  title: string;
  emptyMessage: string;
  endpoint: string;
  refreshKey: number;
}) {
  const { authFetch } = useAuth();
  const [documents, setDocuments] = useState<SourceDocument[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      try {
        const res = await authFetch(endpoint);
        if (res.ok && !cancelled) setDocuments(await res.json());
      } catch {
        // Non-fatal — the rest of the workspace still works if this list fails to load.
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [authFetch, endpoint, refreshKey]);

  return (
    <Card className="flex flex-col gap-3 p-5">
      <h2 className="text-sm font-semibold text-ink">{title}</h2>
      {loading ? (
        <p className="text-sm text-ink-soft">Loading...</p>
      ) : documents.length === 0 ? (
        <p className="text-sm text-ink-soft">{emptyMessage}</p>
      ) : (
        <div className="flex flex-col gap-2">
          {documents.map((doc) => (
            <div
              key={doc.id}
              className="flex items-center justify-between gap-3 rounded-md border border-line bg-paper px-3 py-2.5"
            >
              <div className="flex min-w-0 flex-col">
                <span className="truncate text-sm text-ink">{doc.filename}</span>
                <span className="text-xs text-ink-soft">{formatDate(doc.upload_date)}</span>
              </div>
              <div className="flex shrink-0 items-center gap-1">
                <OpenDocumentButton id={doc.id} filename={doc.filename} />
                <DownloadDocumentButton id={doc.id} filename={doc.filename} />
              </div>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

// --- Root panel --------------------------------------------------------------

export default function TemplatesPanel() {
  const { authFetch } = useAuth();
  const [templates, setTemplates] = useState<TemplateSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [activeTemplateId, setActiveTemplateId] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  // Bumped after a new sample is learned or a new document is generated, so
  // the "Uploaded Templates"/"Generated Documents" lists below re-fetch and
  // pick up the new row without a full page reload.
  const [sampleDocsRefreshKey, setSampleDocsRefreshKey] = useState(0);
  const [generatedDocsRefreshKey, setGeneratedDocsRefreshKey] = useState(0);

  const loadTemplates = useCallback(async () => {
    setLoading(true);
    try {
      const res = await authFetch("/templates");
      if (res.ok) setTemplates(await res.json());
    } catch {
      // Non-fatal — the create-template form still works even if this
      // background list fetch fails.
    } finally {
      setLoading(false);
    }
  }, [authFetch]);

  useEffect(() => {
    loadTemplates();
  }, [loadTemplates]);

  const handleDelete = async (id: string) => {
    if (!window.confirm("Delete this template? This can't be undone.")) return;
    setDeletingId(id);
    try {
      const res = await authFetch(`/templates/${id}`, { method: "DELETE" });
      if (res.ok) {
        setTemplates((prev) => prev.filter((t) => t.id !== id));
        if (activeTemplateId === id) setActiveTemplateId(null);
      }
    } finally {
      setDeletingId(null);
    }
  };

  const activeTemplate = templates.find((t) => t.id === activeTemplateId) ?? null;

  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
      <div className="flex flex-col gap-6">
        <CreateTemplateCard
          onCreated={(template) => {
            setTemplates((prev) => [template, ...prev]);
            setSampleDocsRefreshKey((k) => k + 1);
          }}
        />

        <Card className="flex flex-col gap-3 p-5">
          <h2 className="text-sm font-semibold text-ink">Your templates</h2>
          {loading ? (
            <p className="text-sm text-ink-soft">Loading templates...</p>
          ) : templates.length === 0 ? (
            <p className="text-sm text-ink-soft">
              No templates yet — learn one from a sample Invoice or Agreement above.
            </p>
          ) : (
            <div className="flex flex-col gap-2">
              {templates.map((template) => (
                <div
                  key={template.id}
                  className={cn(
                    "flex items-center justify-between gap-3 rounded-md border px-3 py-2.5",
                    activeTemplateId === template.id ? "border-ink bg-sidebar-bg" : "border-line bg-paper"
                  )}
                >
                  <button
                    type="button"
                    onClick={() => setActiveTemplateId(template.id)}
                    className="flex min-w-0 flex-1 items-center gap-2 text-left"
                  >
                    <FileText className="h-4 w-4 shrink-0 text-ink-soft" strokeWidth={1.75} />
                    <div className="flex min-w-0 flex-col">
                      <span className="truncate text-sm text-ink">{template.name}</span>
                      <span className="text-xs text-ink-soft">
                        {template.category} · {template.field_schema.sections.length} sections
                      </span>
                    </div>
                  </button>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => setActiveTemplateId(template.id)}
                  >
                    <Sparkles className="h-3.5 w-3.5" strokeWidth={1.75} />
                    Generate
                  </Button>
                  <button
                    type="button"
                    onClick={() => handleDelete(template.id)}
                    disabled={deletingId === template.id}
                    aria-label="Delete template"
                    className="shrink-0 rounded p-1.5 text-ink-soft hover:bg-sidebar-bg hover:text-overdue"
                  >
                    <Trash2 className="h-3.5 w-3.5" strokeWidth={1.75} />
                  </button>
                </div>
              ))}
            </div>
          )}
        </Card>

        <SourceDocumentsCard
          title="Uploaded Templates"
          emptyMessage="No sample documents yet — learning a template from a sample uploads it here."
          endpoint="/documents/templates"
          refreshKey={sampleDocsRefreshKey}
        />

        <SourceDocumentsCard
          title="Generated Documents"
          emptyMessage="No generated documents yet — generate one from a template to see it here."
          endpoint="/documents/generated"
          refreshKey={generatedDocsRefreshKey}
        />
      </div>

      <div>
        {activeTemplate ? (
          <GeneratePanel
            template={activeTemplate}
            onClose={() => setActiveTemplateId(null)}
            onGenerated={() => setGeneratedDocsRefreshKey((k) => k + 1)}
          />
        ) : (
          <div className="flex h-full flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-line bg-paper-raised px-6 py-16 text-center">
            <Sparkles className="h-6 w-6 text-ink-soft" strokeWidth={1.5} />
            <p className="text-sm text-ink-soft">Select a template to generate a new document from it.</p>
          </div>
        )}
      </div>
    </div>
  );
}
