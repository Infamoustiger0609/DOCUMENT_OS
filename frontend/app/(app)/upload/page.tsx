"use client";

import { CheckCircle2, Circle, XCircle } from "lucide-react";
import { type ChangeEvent, type DragEvent, useCallback, useEffect, useRef, useState } from "react";

import { Card } from "@/components/ui/card";
import { DropzoneBox, UploadProgressCard } from "@/components/upload-dropzone";
import { useAuth } from "@/lib/auth-context";
import { cn } from "@/lib/utils";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

const ALLOWED_EXTENSIONS = [".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".xlsx"];
const MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024;

// The backend is a single small Render instance — uploading an entire batch
// at once would mean that many concurrent OCR/Groq pipelines competing for
// it. This caps how many *uploads* (the XHR itself) run at once; once a
// file's upload finishes, its poll loop runs independently and doesn't
// count against this — polling is a cheap GET, not the expensive part.
const MAX_CONCURRENT_UPLOADS = 2;

// POST /documents/upload now returns as soon as the file is stored, before the
// OCR/classify/extract pipeline runs (see CLAUDE.md's Background processing
// section) — so its response is never the final state, only ever "uploaded".
// The frontend polls GET /documents/{id} until one of these shows up, the same
// way any other client watching this document would find out it's done.
const TERMINAL_STATUSES = ["processed", "extraction_failed", "classification_failed", "structuring_failed"];
const POLL_INTERVAL_MS = 2000;
const MAX_POLL_ATTEMPTS = 90; // ~3 minutes — well beyond any realistic OCR+Groq run

type EntryState = "queued" | "uploading" | "processing" | "done" | "error";
type StepState = "pending" | "done" | "failed";

interface UploadResult {
  status: string;
  category: string | null;
  error_message: string | null;
}

// One row per selected/dropped file, tracked independently of every other
// row — a batch of files no longer shares a single page-wide upload state.
interface UploadEntry {
  id: string;
  file: File;
  state: EntryState;
  progress: number;
  message: string | null;
  documentId: string | null;
  result: UploadResult | null;
  timedOut: boolean;
}

const STEPS = ["Extracted", "Classified", "Extracting fields", "Filed"] as const;

const AFTER_UPLOAD_STEPS = [
  { title: "Read", body: "Text extracted from every page, including scanned ones." },
  { title: "Sort", body: "Classified into agreement, invoice, GST, or bank statement, and filed." },
  { title: "Extract", body: "Key dates, parties, and amounts pulled into the registry." },
  { title: "Watch", body: "Deadlines show up on the registry and get flagged as they approach." },
] as const;

function getStepStates(status: string | null): StepState[] {
  switch (status) {
    case "extraction_failed":
      return ["failed", "pending", "pending", "pending"];
    case "classification_failed":
      return ["done", "failed", "pending", "pending"];
    case "structuring_failed":
      return ["done", "done", "failed", "pending"];
    case "processed":
      return ["done", "done", "done", "done"];
    case "classified":
      return ["done", "done", "pending", "pending"];
    case "extracted":
      return ["done", "pending", "pending", "pending"];
    default:
      return ["pending", "pending", "pending", "pending"];
  }
}

function getExtension(filename: string): string {
  const idx = filename.lastIndexOf(".");
  return idx === -1 ? "" : filename.slice(idx).toLowerCase();
}

function validateFile(file: File): string | null {
  if (!ALLOWED_EXTENSIONS.includes(getExtension(file.name))) {
    return "Unsupported file type. Allowed: PDF, JPG, PNG, TIFF, Excel (.xlsx).";
  }
  if (file.size > MAX_FILE_SIZE_BYTES) {
    return "File exceeds the 20MB limit.";
  }
  return null;
}

function makeEntryId(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function PipelineStatusCard({
  filename,
  result,
  polling,
  timedOut,
}: {
  filename: string;
  result: UploadResult;
  polling: boolean;
  timedOut: boolean;
}) {
  const states = getStepStates(result.status);

  return (
    <Card className="flex flex-col gap-4 p-5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-sm font-medium text-ink">Processing pipeline</h3>
          <p className="truncate text-xs text-ink-soft">{filename}</p>
        </div>
        {result.category && (
          <span className="shrink-0 text-xs text-ink-soft">Classified as {result.category}</span>
        )}
      </div>
      <div className="flex items-center">
        {STEPS.map((step, index) => {
          const state = states[index];
          return (
            <div key={step} className="flex flex-1 items-center">
              <div className="flex flex-1 flex-col items-center gap-1.5 text-center">
                {state === "done" && (
                  <CheckCircle2 className="h-5 w-5 text-filed" strokeWidth={1.75} />
                )}
                {state === "failed" && <XCircle className="h-5 w-5 text-overdue" strokeWidth={1.75} />}
                {state === "pending" && <Circle className="h-5 w-5 text-muted" strokeWidth={1.75} />}
                <span
                  className={cn(
                    "text-xs",
                    state === "done" && "text-ink",
                    state === "failed" && "text-overdue",
                    state === "pending" && "text-muted"
                  )}
                >
                  {step}
                </span>
              </div>
              {index < STEPS.length - 1 && <div className="h-px flex-1 -translate-y-3 bg-line" />}
            </div>
          );
        })}
      </div>
      {polling && (
        <p className="text-xs text-ink-soft">Still working — this updates automatically.</p>
      )}
      {timedOut && (
        <p className="text-xs text-ink-soft">
          Taking longer than expected — it&apos;s still processing. Check Documents shortly.
        </p>
      )}
      {result.error_message && (
        <p className="rounded-md bg-overdue/10 px-3 py-2 text-sm text-overdue">
          {result.error_message}
        </p>
      )}
    </Card>
  );
}

export default function UploadPage() {
  const { token, authFetch, logout } = useAuth();
  const [entries, setEntries] = useState<UploadEntry[]>([]);
  const [isDragging, setIsDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  // Per-entry poll timers — a batch upload means several independent poll
  // loops running at once, not just one.
  const pollTimeoutsRef = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());
  // The concurrency gate: files waiting for an upload slot, and how many
  // uploads are currently in flight. Plain refs, not state — this is
  // internal scheduling, not something the UI renders directly.
  const uploadQueueRef = useRef<{ id: string; file: File }[]>([]);
  const activeUploadsRef = useRef(0);

  // Cancel every in-flight poll loop if the user navigates away mid-upload.
  useEffect(() => {
    const timeouts = pollTimeoutsRef.current;
    return () => {
      timeouts.forEach((t) => clearTimeout(t));
      timeouts.clear();
    };
  }, []);

  const updateEntry = useCallback((id: string, patch: Partial<UploadEntry>) => {
    setEntries((prev) => prev.map((e) => (e.id === id ? { ...e, ...patch } : e)));
  }, []);

  const pollDocumentStatus = useCallback(
    (id: string, documentId: string, attempt: number) => {
      authFetch(`/documents/${documentId}`)
        .then((res) => {
          if (!res.ok) throw new Error("poll failed");
          return res.json();
        })
        .then((data: { status: string; category: string | null; error_message: string | null }) => {
          updateEntry(id, {
            result: { status: data.status, category: data.category, error_message: data.error_message },
          });

          if (TERMINAL_STATUSES.includes(data.status)) {
            updateEntry(id, { state: "done" });
            return;
          }
          if (attempt >= MAX_POLL_ATTEMPTS) {
            updateEntry(id, { state: "done", timedOut: true });
            return;
          }
          pollTimeoutsRef.current.set(
            id,
            setTimeout(() => pollDocumentStatus(id, documentId, attempt + 1), POLL_INTERVAL_MS)
          );
        })
        .catch(() => {
          // A transient network hiccup shouldn't abandon the poll over
          // something that's likely still succeeding server-side — retry on
          // the same schedule instead of surfacing an error immediately.
          if (attempt >= MAX_POLL_ATTEMPTS) {
            updateEntry(id, { state: "done", timedOut: true });
            return;
          }
          pollTimeoutsRef.current.set(
            id,
            setTimeout(() => pollDocumentStatus(id, documentId, attempt + 1), POLL_INTERVAL_MS)
          );
        });
    },
    [authFetch, updateEntry]
  );

  // Runs the actual XHR for one file. Resolves as soon as the upload itself
  // settles (success or failure) — never waits for polling — so the
  // concurrency gate below can immediately hand the freed slot to the next
  // queued file instead of waiting out that file's whole pipeline run.
  const runUpload = useCallback(
    (id: string, file: File): Promise<void> => {
      return new Promise((resolve) => {
        const validationError = validateFile(file);
        if (validationError) {
          updateEntry(id, { state: "error", message: validationError });
          resolve();
          return;
        }

        updateEntry(id, { state: "uploading", progress: 0, message: null });

        const formData = new FormData();
        formData.append("file", file);

        const xhr = new XMLHttpRequest();
        xhr.open("POST", `${API_URL}/documents/upload`);
        if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);

        xhr.upload.onprogress = (event) => {
          if (event.lengthComputable) {
            updateEntry(id, { progress: Math.round((event.loaded / event.total) * 100) });
          }
        };

        xhr.onload = () => {
          if (xhr.status === 401) {
            logout();
            resolve();
            return;
          }

          let data: {
            id?: string;
            status?: string;
            category?: string | null;
            error_message?: string | null;
            detail?: string;
          } = {};
          try {
            data = JSON.parse(xhr.responseText);
          } catch {
            // ignore, fall through to status-code handling below
          }

          if (xhr.status >= 200 && xhr.status < 300 && data.status && data.id) {
            const documentId = data.id;
            const result: UploadResult = {
              status: data.status,
              category: data.category ?? null,
              error_message: data.error_message ?? null,
            };

            if (TERMINAL_STATUSES.includes(data.status)) {
              updateEntry(id, { state: "done", documentId, result });
            } else {
              updateEntry(id, { state: "processing", documentId, result });
              pollTimeoutsRef.current.set(
                id,
                setTimeout(() => pollDocumentStatus(id, documentId, 1), POLL_INTERVAL_MS)
              );
            }
          } else {
            updateEntry(id, { state: "error", message: data.detail ?? "Upload failed." });
          }
          resolve();
        };

        xhr.onerror = () => {
          updateEntry(id, { state: "error", message: "Upload failed. Could not reach the backend." });
          resolve();
        };

        xhr.send(formData);
      });
    },
    [token, logout, updateEntry, pollDocumentStatus]
  );

  const pump = useCallback(() => {
    while (activeUploadsRef.current < MAX_CONCURRENT_UPLOADS && uploadQueueRef.current.length > 0) {
      const next = uploadQueueRef.current.shift()!;
      activeUploadsRef.current += 1;
      runUpload(next.id, next.file).finally(() => {
        activeUploadsRef.current -= 1;
        pump();
      });
    }
  }, [runUpload]);

  const enqueueFiles = useCallback(
    (files: File[]) => {
      if (files.length === 0) return;
      const newEntries: UploadEntry[] = files.map((file) => ({
        id: makeEntryId(),
        file,
        state: "queued",
        progress: 0,
        message: null,
        documentId: null,
        result: null,
        timedOut: false,
      }));
      setEntries((prev) => [...prev, ...newEntries]);
      uploadQueueRef.current.push(...newEntries.map((e) => ({ id: e.id, file: e.file })));
      pump();
    },
    [pump]
  );

  const handleDrop = useCallback(
    (event: DragEvent<HTMLDivElement>) => {
      event.preventDefault();
      setIsDragging(false);
      enqueueFiles(Array.from(event.dataTransfer.files ?? []));
    },
    [enqueueFiles]
  );

  const handleFileChange = useCallback(
    (event: ChangeEvent<HTMLInputElement>) => {
      enqueueFiles(Array.from(event.target.files ?? []));
      event.target.value = "";
    },
    [enqueueFiles]
  );

  return (
    <div className="flex flex-col gap-8 px-10 py-10">
      <div>
        <h1 className="font-serif text-2xl font-semibold text-ink">Upload documents</h1>
        <p className="mt-1 text-sm text-ink-soft">
          Each one is read, sorted, and filed automatically — usually in under a minute
        </p>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[58%_42%]">
        <div className="flex flex-col gap-6">
          <DropzoneBox
            isDragging={isDragging}
            onDragOver={(event) => {
              event.preventDefault();
              setIsDragging(true);
            }}
            onDragLeave={() => setIsDragging(false)}
            onDrop={handleDrop}
            onChooseFile={() => inputRef.current?.click()}
            fileInputRef={inputRef}
            onFileChange={handleFileChange}
          />

          {entries.map((entry) => (
            <div key={entry.id}>
              {entry.state === "queued" && (
                <Card className="flex items-center justify-between gap-3 p-5">
                  <span className="truncate text-sm text-ink">{entry.file.name}</span>
                  <span className="shrink-0 text-xs text-ink-soft">Waiting to upload…</span>
                </Card>
              )}

              {entry.state === "uploading" && (
                <UploadProgressCard progress={entry.progress} filename={entry.file.name} />
              )}

              {entry.state === "error" && entry.message && (
                <p className="rounded-md bg-overdue/10 px-3 py-2 text-sm text-overdue">
                  <span className="font-medium">{entry.file.name}: </span>
                  {entry.message}
                </p>
              )}

              {(entry.state === "processing" || entry.state === "done") && entry.result && (
                <PipelineStatusCard
                  filename={entry.file.name}
                  result={entry.result}
                  polling={entry.state === "processing"}
                  timedOut={entry.timedOut}
                />
              )}
            </div>
          ))}
        </div>

        <div className="relative overflow-hidden rounded-lg bg-ink px-6 py-8">
          <div
            className="pointer-events-none absolute -right-10 -top-10 h-40 w-40 rounded-full"
            style={{
              backgroundImage: "radial-gradient(circle, rgba(184,132,46,0.35) 0%, transparent 70%)",
            }}
          />
          <h3 className="relative z-10 font-serif text-lg font-medium text-paper">
            What happens after you upload
          </h3>
          <ol className="relative z-10 mt-6 flex flex-col gap-6">
            {AFTER_UPLOAD_STEPS.map((step, index) => (
              <li key={step.title} className="flex gap-3">
                <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-paper/30 text-xs font-medium text-paper/90">
                  {index + 1}
                </span>
                <div>
                  <p className="text-sm font-medium text-paper/90">{step.title}</p>
                  <p className="mt-0.5 text-sm text-paper/60">{step.body}</p>
                </div>
              </li>
            ))}
          </ol>
        </div>
      </div>
    </div>
  );
}
