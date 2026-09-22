"use client";

import {
  ChevronDown,
  ChevronUp,
  Combine,
  Download,
  FileText,
  Image as ImageIcon,
  Maximize2,
  Minimize2,
  MessageSquare,
  Repeat,
  ScanSearch,
  Scissors,
  Send,
  UploadCloud,
  Wrench,
  X,
} from "lucide-react";
import {
  type ChangeEvent,
  type DragEvent,
  type FormEvent,
  type ReactNode,
  useCallback,
  useEffect,
  useState,
} from "react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useAuth } from "@/lib/auth-context";
import { cn } from "@/lib/utils";

import TemplatesPanel from "./TemplatesPanel";

// This page is deliberately separate from the Documents registry/upload pipeline (see
// CLAUDE.md's Document tools section): files added here are pure client-side
// state until a tool is actually run, never touch classification/extraction,
// and the resulting output is a /tools/* ToolFileOut, not a `documents` row.
const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

const PDF_EXT = ".pdf";
const DOCX_EXT = ".docx";
const IMAGE_EXTS = [".jpg", ".jpeg", ".png", ".tif", ".tiff"] as const;
const ALL_ALLOWED_EXTS = [PDF_EXT, DOCX_EXT, ...IMAGE_EXTS];
// Matches tools_router.TOOLS_MAX_FILE_SIZE_BYTES on the backend — larger than
// the main pipeline's 20MB cap since compress-pdf specifically targets large files.
const MAX_FILE_SIZE_BYTES = 100 * 1024 * 1024;

type ToolKey = "merge" | "split" | "compress" | "convert" | "resize" | "ocr";
type SelectionMode = "single" | "multi";
type ImageFormat = "jpg" | "png" | "tiff";

interface EditorFile {
  id: string;
  file: File;
}

interface ToolFileResult {
  id: string;
  output_filename: string;
  mime_type: string;
  size_bytes: number;
}

interface RunState {
  status: "idle" | "running" | "done" | "error";
  progress: number;
  error: string | null;
  results: ToolFileResult[];
}

// A file the assistant (or a manual tool run) has already produced this
// session — kept around so a later chat message can say "now compress that"
// without re-uploading anything; sent as /editor/chat's context_tool_files.
interface KnownToolFile {
  id: string;
  filename: string;
}

interface EditorChatToolRun {
  tool_name: string;
  files: ToolFileResult[];
  error: string | null;
}

interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  toolRuns?: EditorChatToolRun[];
}

// Maps editor_chat.py's function-calling names (e.g. "merge_pdf") to the same
// human labels TOOLS above uses for the manual picker — the chat panel and
// the manual tool picker are two ways into the exact same backend tools, so
// they should read as the same vocabulary to the user.
const TOOL_RUN_LABELS: Record<string, string> = {
  merge_pdf: "Merge",
  split_pdf: "Split",
  compress_pdf: "Compress",
  pdf_to_docx: "Convert",
  docx_to_pdf: "Convert",
  convert_image: "Convert",
  resize_image: "Resize",
  ocr_pdf: "OCR",
};

const IDLE_RUN: RunState = { status: "idle", progress: 0, error: null, results: [] };

const TOOLS: { key: ToolKey; label: string; icon: typeof Combine; description: string }[] = [
  { key: "merge", label: "Merge", icon: Combine, description: "Combine 2 or more PDFs into one, in the order you choose." },
  { key: "split", label: "Split", icon: Scissors, description: "Split one PDF into individual pages, or by page range." },
  { key: "compress", label: "Compress", icon: Minimize2, description: "Shrink a PDF's file size." },
  { key: "convert", label: "Convert", icon: Repeat, description: "PDF ↔ DOCX, or between JPG/PNG/TIFF." },
  { key: "resize", label: "Resize", icon: Maximize2, description: "Resize an image by dimensions or target file size." },
  { key: "ocr", label: "OCR", icon: ScanSearch, description: "Make a scanned PDF searchable by adding an invisible text layer." },
];

const SELECTION_MODE: Record<ToolKey, SelectionMode> = {
  merge: "multi",
  split: "single",
  compress: "single",
  convert: "single",
  resize: "single",
  ocr: "single",
};

function getExtension(filename: string): string {
  const idx = filename.lastIndexOf(".");
  return idx === -1 ? "" : filename.slice(idx).toLowerCase();
}

function isEligible(tool: ToolKey, filename: string): boolean {
  const ext = getExtension(filename);
  if (tool === "merge" || tool === "split" || tool === "compress" || tool === "ocr") return ext === PDF_EXT;
  if (tool === "resize") return (IMAGE_EXTS as readonly string[]).includes(ext);
  return ext === PDF_EXT || ext === DOCX_EXT || (IMAGE_EXTS as readonly string[]).includes(ext);
}

function normalizedImageFormat(ext: string): ImageFormat {
  if (ext === ".png") return "png";
  if (ext === ".tif" || ext === ".tiff") return "tiff";
  return "jpg";
}

function convertModeFor(filename: string): "pdf-to-docx" | "docx-to-pdf" | "convert-image" {
  const ext = getExtension(filename);
  if (ext === PDF_EXT) return "pdf-to-docx";
  if (ext === DOCX_EXT) return "docx-to-pdf";
  return "convert-image";
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function FileTypeIcon({ filename }: { filename: string }) {
  const ext = getExtension(filename);
  if ((IMAGE_EXTS as readonly string[]).includes(ext)) {
    return <ImageIcon className="h-4 w-4 shrink-0 text-ink-soft" strokeWidth={1.75} />;
  }
  return <FileText className="h-4 w-4 shrink-0 text-ink-soft" strokeWidth={1.75} />;
}

function ToolDownloadButton({ toolFileId }: { toolFileId: string }) {
  const { authFetch } = useAuth();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleClick = async () => {
    // Open the tab synchronously on click — a popup opened after an await is
    // treated as not user-initiated and gets blocked. Same pattern as
    // documents/[id]/page.tsx's ViewOriginalButton.
    const newTab = window.open("", "_blank");
    if (newTab) newTab.opener = null;

    setLoading(true);
    setError(null);
    try {
      if (!newTab) {
        throw new Error("Your browser blocked the popup. Allow popups for this site and try again.");
      }
      const res = await authFetch(`/tools/${toolFileId}/download-url`);
      if (!res.ok) {
        const data = await res.json().catch(() => null);
        throw new Error(data?.detail ?? "Could not get a download link.");
      }
      const data: { url: string } = await res.json();
      newTab.location.href = data.url;
    } catch (err) {
      newTab?.close();
      setError(err instanceof Error ? err.message : "Could not get a download link.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex flex-col items-end gap-1">
      <Button type="button" variant="outline" size="sm" onClick={handleClick} disabled={loading}>
        <Download className="h-3.5 w-3.5" strokeWidth={1.75} />
        {loading ? "Opening..." : "Download"}
      </Button>
      {error && <span className="max-w-[220px] text-right text-xs text-overdue">{error}</span>}
    </div>
  );
}

function OptionRow({ label, description, children }: { label: string; description?: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5">
      <Label>{label}</Label>
      {children}
      {description && <p className="text-xs text-ink-soft">{description}</p>}
    </div>
  );
}

function ToolRunCard({ run }: { run: EditorChatToolRun }) {
  const label = TOOL_RUN_LABELS[run.tool_name] ?? run.tool_name;
  return (
    <div
      className={cn(
        "rounded-md border px-2.5 py-2 text-xs",
        run.error ? "border-overdue/30 bg-overdue/5" : "border-line bg-paper"
      )}
    >
      <div className="flex items-center gap-1.5 font-medium text-ink">
        <Wrench className="h-3 w-3" strokeWidth={1.75} />
        {label}
      </div>
      {run.error ? (
        <p className="mt-1 text-overdue">{run.error}</p>
      ) : (
        <div className="mt-1.5 flex flex-col gap-1.5">
          {run.files.map((f) => (
            <div key={f.id} className="flex items-center justify-between gap-2">
              <span className="min-w-0 truncate text-ink-soft">
                {f.output_filename} · {formatBytes(f.size_bytes)}
              </span>
              <ToolDownloadButton toolFileId={f.id} />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// Same visual pattern as documents/[id]/page.tsx's ChatCard (the main
// document-detail Q&A panel) — a sticky bubble-list card with an input at
// the bottom — extended here to also render tool-run result cards under an
// assistant message, since this assistant's "answers" are often "I ran X."
function EditorChatPanel({
  files,
  knownToolFiles,
  onNewToolFiles,
}: {
  files: EditorFile[];
  knownToolFiles: KnownToolFile[];
  onNewToolFiles: (files: KnownToolFile[]) => void;
}) {
  const { authFetch } = useAuth();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSend = useCallback(
    async (event: FormEvent) => {
      event.preventDefault();
      const trimmed = input.trim();
      if (!trimmed || sending) return;

      const history = messages.map((m) => ({ role: m.role, content: m.content }));
      setMessages((prev) => [...prev, { role: "user", content: trimmed }]);
      setInput("");
      setSending(true);
      setError(null);

      const formData = new FormData();
      formData.append("message", trimmed);
      files.forEach((f) => formData.append("files", f.file, f.file.name));
      formData.append("history", JSON.stringify(history));
      formData.append("context_tool_files", JSON.stringify(knownToolFiles));

      try {
        const res = await authFetch("/editor/chat", { method: "POST", body: formData });
        if (!res.ok) {
          const data = await res.json().catch(() => null);
          throw new Error(data?.detail ?? "Could not get a response.");
        }
        const data: { reply: string; tool_runs: EditorChatToolRun[] } = await res.json();
        setMessages((prev) => [...prev, { role: "assistant", content: data.reply, toolRuns: data.tool_runs }]);
        const produced = data.tool_runs.flatMap((r) => r.files.map((f) => ({ id: f.id, filename: f.output_filename })));
        if (produced.length) onNewToolFiles(produced);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Could not get a response.");
      } finally {
        setSending(false);
      }
    },
    [input, sending, messages, files, knownToolFiles, authFetch, onNewToolFiles]
  );

  return (
    <div className="flex flex-col rounded-lg border border-line bg-paper-raised lg:sticky lg:top-8">
      <div className="flex items-center gap-2 border-b border-line px-4 py-3.5">
        <MessageSquare className="h-4 w-4 text-ink" strokeWidth={1.75} />
        <span className="text-sm font-semibold text-ink">Ask the editor</span>
      </div>

      <div className="flex max-h-[60vh] min-h-[200px] flex-col gap-3 overflow-y-auto px-4 py-4">
        {messages.length === 0 ? (
          <p className="text-sm text-ink-soft">
            {files.length === 0
              ? "Add files above, then describe what you'd like done — e.g. \"merge these and compress the result under 5MB.\""
              : "Describe what you'd like done with your files in plain language."}
          </p>
        ) : (
          messages.map((message, index) => (
            <div key={index} className={cn("flex flex-col gap-1.5", message.role === "user" ? "items-end" : "items-start")}>
              <div
                className={cn(
                  "max-w-[90%] whitespace-pre-wrap rounded-xl px-3.5 py-2.5 text-sm leading-relaxed",
                  message.role === "user"
                    ? "rounded-br-sm bg-ink text-paper"
                    : "rounded-bl-sm bg-sidebar-bg text-ink"
                )}
              >
                {message.content}
              </div>
              {message.toolRuns && message.toolRuns.length > 0 && (
                <div className="flex w-[90%] flex-col gap-1.5">
                  {message.toolRuns.map((run, runIndex) => (
                    <ToolRunCard key={runIndex} run={run} />
                  ))}
                </div>
              )}
            </div>
          ))
        )}
        {sending && (
          <div className="self-start rounded-xl rounded-bl-sm bg-sidebar-bg px-3.5 py-2.5 text-sm text-ink-soft">
            Working on it...
          </div>
        )}
      </div>

      {error && <p className="px-4 pb-2 text-xs text-overdue">{error}</p>}

      <form onSubmit={handleSend} className="flex items-center gap-2 border-t border-line p-3.5">
        <Input
          type="text"
          placeholder="e.g. convert sample.docx to PDF"
          value={input}
          onChange={(event) => setInput(event.target.value)}
          disabled={sending}
          className="flex-1"
        />
        <Button type="submit" size="icon" disabled={sending || !input.trim()}>
          <Send className="h-4 w-4" strokeWidth={1.75} />
        </Button>
      </form>
    </div>
  );
}

type WorkspaceMode = "tools" | "templates";

export default function WorkspacePage() {
  const { token, logout } = useAuth();

  const [mode, setMode] = useState<WorkspaceMode>("tools");

  const [files, setFiles] = useState<EditorFile[]>([]);
  const [addError, setAddError] = useState<string | null>(null);
  const [isDragging, setIsDragging] = useState(false);

  const [tool, setTool] = useState<ToolKey>("merge");
  const [selectedIds, setSelectedIds] = useState<string[]>([]);

  const [splitRanges, setSplitRanges] = useState("");
  const [quality, setQuality] = useState<"low" | "medium" | "high">("medium");
  const [targetFormat, setTargetFormat] = useState<ImageFormat>("jpg");
  const [resizeWidth, setResizeWidth] = useState("");
  const [resizeHeight, setResizeHeight] = useState("");
  const [resizeTargetSizeKb, setResizeTargetSizeKb] = useState("");
  const [forceOcr, setForceOcr] = useState(false);

  const [run, setRun] = useState<RunState>(IDLE_RUN);
  const [knownToolFiles, setKnownToolFiles] = useState<KnownToolFile[]>([]);

  // Drop anything no longer eligible (or, for single-select tools, anything
  // past the first) whenever the active tool changes.
  useEffect(() => {
    setRun(IDLE_RUN);
    setSelectedIds((prev) => {
      const eligible = prev.filter((id) => {
        const found = files.find((f) => f.id === id);
        return found && isEligible(tool, found.file.name);
      });
      return SELECTION_MODE[tool] === "single" ? eligible.slice(0, 1) : eligible;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tool]);

  const selectedFile = selectedIds.length > 0 ? files.find((f) => f.id === selectedIds[0]) ?? null : null;

  // Keep the image-convert target format valid (never the source's own format).
  useEffect(() => {
    if (tool !== "convert" || !selectedFile || convertModeFor(selectedFile.file.name) !== "convert-image") return;
    const source = normalizedImageFormat(getExtension(selectedFile.file.name));
    if (targetFormat === source) {
      const fallback = (["jpg", "png", "tiff"] as ImageFormat[]).find((f) => f !== source);
      if (fallback) setTargetFormat(fallback);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tool, selectedFile]);

  const addFiles = (incoming: FileList | File[]) => {
    const accepted: EditorFile[] = [];
    const rejected: string[] = [];
    for (const file of Array.from(incoming)) {
      const ext = getExtension(file.name);
      if (!ALL_ALLOWED_EXTS.includes(ext)) {
        rejected.push(`${file.name}: unsupported file type`);
      } else if (file.size === 0) {
        rejected.push(`${file.name}: file is empty`);
      } else if (file.size > MAX_FILE_SIZE_BYTES) {
        rejected.push(`${file.name}: exceeds the 100MB limit`);
      } else {
        accepted.push({ id: crypto.randomUUID(), file });
      }
    }
    if (accepted.length) setFiles((prev) => [...prev, ...accepted]);
    setAddError(rejected.length ? rejected.join("; ") : null);
  };

  const removeFile = (id: string) => {
    setFiles((prev) => prev.filter((f) => f.id !== id));
    setSelectedIds((prev) => prev.filter((x) => x !== id));
  };

  const clearAll = () => {
    setFiles([]);
    setSelectedIds([]);
    setRun(IDLE_RUN);
  };

  const toggleSelect = (id: string) => {
    setRun(IDLE_RUN);
    if (SELECTION_MODE[tool] === "single") {
      setSelectedIds((prev) => (prev[0] === id ? [] : [id]));
    } else {
      setSelectedIds((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
    }
  };

  const moveSelected = (id: string, direction: -1 | 1) => {
    setSelectedIds((prev) => {
      const idx = prev.indexOf(id);
      const nextIdx = idx + direction;
      if (idx === -1 || nextIdx < 0 || nextIdx >= prev.length) return prev;
      const copy = [...prev];
      [copy[idx], copy[nextIdx]] = [copy[nextIdx]!, copy[idx]!];
      return copy;
    });
  };

  const resizeParamsProvided = Boolean(resizeWidth || resizeHeight || resizeTargetSizeKb);
  const isSelectionValid =
    tool === "merge"
      ? selectedIds.length >= 2
      : tool === "resize"
        ? selectedIds.length === 1 && resizeParamsProvided
        : selectedIds.length === 1;

  const handleRun = () => {
    if (!isSelectionValid || run.status === "running") return;

    const formData = new FormData();
    let endpoint = "";

    if (tool === "merge") {
      endpoint = "/tools/merge-pdf";
      selectedIds.forEach((id) => {
        const f = files.find((x) => x.id === id);
        if (f) formData.append("files", f.file, f.file.name);
      });
    } else if (selectedFile) {
      formData.append("file", selectedFile.file, selectedFile.file.name);

      if (tool === "split") {
        endpoint = "/tools/split-pdf";
        if (splitRanges.trim()) formData.append("ranges", splitRanges.trim());
      } else if (tool === "compress") {
        endpoint = "/tools/compress-pdf";
        formData.append("quality", quality);
      } else if (tool === "convert") {
        const mode = convertModeFor(selectedFile.file.name);
        if (mode === "pdf-to-docx") endpoint = "/tools/pdf-to-docx";
        else if (mode === "docx-to-pdf") endpoint = "/tools/docx-to-pdf";
        else {
          endpoint = "/tools/convert-image";
          formData.append("target_format", targetFormat);
        }
      } else if (tool === "resize") {
        endpoint = "/tools/resize-image";
        if (resizeWidth) formData.append("width", resizeWidth);
        if (resizeHeight) formData.append("height", resizeHeight);
        if (resizeTargetSizeKb) formData.append("target_size_kb", resizeTargetSizeKb);
      } else if (tool === "ocr") {
        endpoint = "/tools/ocr-pdf";
        if (forceOcr) formData.append("force_ocr", "true");
      }
    }

    if (!endpoint) return;
    setRun({ status: "running", progress: 0, error: null, results: [] });

    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API_URL}${endpoint}`);
    if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);

    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) {
        const progress = Math.round((event.loaded / event.total) * 100);
        setRun((prev) => (prev.status === "running" ? { ...prev, progress } : prev));
      }
    };

    xhr.onload = () => {
      if (xhr.status === 401) {
        logout();
        return;
      }
      let data: (ToolFileResult & { detail?: string }) | ToolFileResult[] | { detail?: string } | null = null;
      try {
        data = JSON.parse(xhr.responseText);
      } catch {
        // fall through to status-code handling below
      }

      if (xhr.status >= 200 && xhr.status < 300 && data) {
        const results = Array.isArray(data) ? data : [data as ToolFileResult];
        setRun({ status: "done", progress: 100, error: null, results });
        // Also make this available to the chat panel as something it can
        // reference later ("now compress that") without re-uploading it.
        setKnownToolFiles((prev) => [
          ...prev,
          ...results.map((r) => ({ id: r.id, filename: r.output_filename })),
        ]);
      } else {
        const detail = data && !Array.isArray(data) ? (data as { detail?: string }).detail : undefined;
        setRun({ status: "error", progress: 0, error: detail ?? "The tool failed to run.", results: [] });
      }
    };

    xhr.onerror = () => {
      setRun({ status: "error", progress: 0, error: "Could not reach the backend.", results: [] });
    };

    xhr.send(formData);
  };

  return (
    <div className="flex flex-col gap-8 px-10 py-10">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="font-serif text-2xl font-semibold text-ink">Editing Workspace</h1>
          <p className="mt-1 text-sm text-ink-soft">
            {mode === "tools"
              ? "Merge, split, compress, and convert files directly — nothing here is filed or classified."
              : "Learn a reusable field structure from a sample document, then generate new ones with fresh values."}
          </p>
        </div>
        <div className="flex w-fit gap-1 rounded-md border border-line bg-paper p-1">
          {(["tools", "templates"] as const).map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => setMode(m)}
              className={cn(
                "rounded px-3.5 py-1.5 text-sm font-medium capitalize transition-colors",
                mode === m ? "bg-ink text-paper" : "text-ink-soft hover:bg-sidebar-bg"
              )}
            >
              {m}
            </button>
          ))}
        </div>
      </div>

      {mode === "templates" && <TemplatesPanel />}

      {mode === "tools" && (
      <>
      <div
        onDragOver={(event: DragEvent<HTMLDivElement>) => {
          event.preventDefault();
          setIsDragging(true);
        }}
        onDragLeave={() => setIsDragging(false)}
        onDrop={(event: DragEvent<HTMLDivElement>) => {
          event.preventDefault();
          setIsDragging(false);
          if (event.dataTransfer.files?.length) addFiles(event.dataTransfer.files);
        }}
        className={cn(
          "relative flex flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed bg-paper-raised px-6 py-10 text-center transition-colors",
          isDragging ? "border-filed" : "border-line"
        )}
      >
        <UploadCloud className="h-8 w-8 text-ink-soft" strokeWidth={1.5} />
        <p className="text-sm text-ink">Drop files, or browse — add as many as you need</p>
        <p className="text-xs text-muted">PDF, DOCX, JPG, PNG, TIFF — up to 100MB each</p>
        <label className="mt-2">
          <span className="inline-flex h-10 cursor-pointer items-center justify-center rounded-md border border-line bg-paper-raised px-4 text-sm font-medium text-ink transition-colors hover:bg-sidebar-bg">
            Choose files
          </span>
          <input
            type="file"
            multiple
            accept={ALL_ALLOWED_EXTS.join(",")}
            className="hidden"
            onChange={(event: ChangeEvent<HTMLInputElement>) => {
              if (event.target.files?.length) addFiles(event.target.files);
              event.target.value = "";
            }}
          />
        </label>
      </div>

      {addError && <p className="rounded-md bg-overdue/10 px-3 py-2 text-sm text-overdue">{addError}</p>}

      {files.length > 0 && (
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,0.85fr)_minmax(0,1fr)_minmax(0,0.9fr)]">
          <Card className="flex flex-col overflow-hidden">
            <div className="flex items-center justify-between border-b border-line px-4 py-3">
              <h2 className="text-sm font-semibold text-ink">Files ({files.length})</h2>
              <button type="button" onClick={clearAll} className="text-xs text-ink-soft hover:text-ink">
                Clear all
              </button>
            </div>
            <div className="flex max-h-[480px] flex-col overflow-y-auto">
              {files.map((editorFile) => {
                const eligible = isEligible(tool, editorFile.file.name);
                const isSelected = selectedIds.includes(editorFile.id);
                const order = selectedIds.indexOf(editorFile.id) + 1;
                const mode = SELECTION_MODE[tool];
                return (
                  <div
                    key={editorFile.id}
                    className={cn(
                      "flex items-center gap-3 border-b border-line px-4 py-3 last:border-b-0",
                      !eligible && "opacity-40"
                    )}
                  >
                    <button
                      type="button"
                      onClick={() => eligible && toggleSelect(editorFile.id)}
                      disabled={!eligible}
                      aria-label={isSelected ? "Deselect" : "Select"}
                      className={cn(
                        "flex h-5 w-5 shrink-0 items-center justify-center border text-[10px] font-semibold transition-colors disabled:cursor-not-allowed",
                        mode === "single" ? "rounded-full" : "rounded",
                        isSelected ? "border-ink bg-ink text-paper" : "border-line bg-paper text-transparent"
                      )}
                    >
                      {isSelected && (mode === "multi" ? order : "✓")}
                    </button>

                    <FileTypeIcon filename={editorFile.file.name} />

                    <div className="flex min-w-0 flex-1 flex-col">
                      <span className="truncate text-sm text-ink">{editorFile.file.name}</span>
                      <span className="text-xs text-ink-soft">
                        {formatBytes(editorFile.file.size)}
                        {!eligible && ` · not usable with ${TOOLS.find((t) => t.key === tool)?.label}`}
                      </span>
                    </div>

                    {mode === "multi" && isSelected && (
                      <div className="flex shrink-0 gap-0.5">
                        <button
                          type="button"
                          onClick={() => moveSelected(editorFile.id, -1)}
                          disabled={order <= 1}
                          aria-label="Move up"
                          className="rounded p-1 text-ink-soft hover:bg-sidebar-bg disabled:opacity-30"
                        >
                          <ChevronUp className="h-3.5 w-3.5" strokeWidth={1.75} />
                        </button>
                        <button
                          type="button"
                          onClick={() => moveSelected(editorFile.id, 1)}
                          disabled={order >= selectedIds.length}
                          aria-label="Move down"
                          className="rounded p-1 text-ink-soft hover:bg-sidebar-bg disabled:opacity-30"
                        >
                          <ChevronDown className="h-3.5 w-3.5" strokeWidth={1.75} />
                        </button>
                      </div>
                    )}

                    <button
                      type="button"
                      onClick={() => removeFile(editorFile.id)}
                      aria-label="Remove file"
                      className="shrink-0 rounded p-1 text-ink-soft hover:bg-sidebar-bg hover:text-overdue"
                    >
                      <X className="h-3.5 w-3.5" strokeWidth={1.75} />
                    </button>
                  </div>
                );
              })}
            </div>
          </Card>

          <div className="flex flex-col gap-6">
            <Card className="flex flex-col gap-3 p-4">
              <div className="grid grid-cols-6 gap-1.5">
                {TOOLS.map((t) => {
                  const Icon = t.icon;
                  return (
                    <button
                      key={t.key}
                      type="button"
                      onClick={() => setTool(t.key)}
                      className={cn(
                        "flex flex-col items-center gap-1.5 rounded-md px-2 py-3 text-xs font-medium transition-colors",
                        tool === t.key ? "bg-ink text-paper" : "text-ink-soft hover:bg-sidebar-bg"
                      )}
                    >
                      <Icon className="h-4 w-4" strokeWidth={1.75} />
                      {t.label}
                    </button>
                  );
                })}
              </div>
              <p className="text-xs text-ink-soft">{TOOLS.find((t) => t.key === tool)?.description}</p>
            </Card>

            <Card className="flex flex-col gap-4 p-5">
              {tool === "merge" && (
                <p className="text-sm text-ink-soft">
                  {selectedIds.length < 2
                    ? "Select 2 or more PDFs on the left, in the order you want them merged."
                    : `${selectedIds.length} PDFs selected — reorder with the arrows on the left.`}
                </p>
              )}

              {tool === "split" && (
                <OptionRow
                  label="Page ranges (optional)"
                  description='e.g. "1-3,5,7-9". Leave blank to split into one PDF per page.'
                >
                  <Input
                    value={splitRanges}
                    onChange={(event) => setSplitRanges(event.target.value)}
                    placeholder="1-3,5,7-9"
                    disabled={!selectedFile}
                  />
                </OptionRow>
              )}

              {tool === "compress" && (
                <OptionRow
                  label="Compression level"
                  description={
                    quality === "low"
                      ? "Screen quality (~72dpi) — smallest file, most aggressive."
                      : quality === "medium"
                        ? "Ebook quality (~150dpi) — balanced, good default."
                        : "Printer quality (~300dpi) — largest, best fidelity."
                  }
                >
                  <div className="flex w-fit gap-1 rounded-md border border-line bg-paper p-1">
                    {(["low", "medium", "high"] as const).map((q) => (
                      <button
                        key={q}
                        type="button"
                        onClick={() => setQuality(q)}
                        className={cn(
                          "rounded px-3 py-1.5 text-sm capitalize transition-colors",
                          quality === q ? "bg-ink text-paper" : "text-ink-soft hover:bg-sidebar-bg"
                        )}
                      >
                        {q}
                      </button>
                    ))}
                  </div>
                </OptionRow>
              )}

              {tool === "convert" &&
                (selectedFile ? (
                  convertModeFor(selectedFile.file.name) === "convert-image" ? (
                    <OptionRow label="Convert to">
                      <div className="flex w-fit gap-1 rounded-md border border-line bg-paper p-1">
                        {(["jpg", "png", "tiff"] as ImageFormat[])
                          .filter((f) => f !== normalizedImageFormat(getExtension(selectedFile.file.name)))
                          .map((f) => (
                            <button
                              key={f}
                              type="button"
                              onClick={() => setTargetFormat(f)}
                              className={cn(
                                "rounded px-3 py-1.5 text-sm uppercase transition-colors",
                                targetFormat === f ? "bg-ink text-paper" : "text-ink-soft hover:bg-sidebar-bg"
                              )}
                            >
                              {f}
                            </button>
                          ))}
                      </div>
                    </OptionRow>
                  ) : (
                    <p className="text-sm text-ink-soft">
                      {convertModeFor(selectedFile.file.name) === "pdf-to-docx"
                        ? "This PDF will be converted to a DOCX file."
                        : "This DOCX will be converted to a PDF file."}
                    </p>
                  )
                ) : (
                  <p className="text-sm text-ink-soft">Select a PDF, DOCX, JPG, PNG, or TIFF on the left.</p>
                ))}

              {tool === "resize" && (
                <div className="flex flex-col gap-4">
                  <div className="grid grid-cols-2 gap-4">
                    <OptionRow label="Width (px)">
                      <Input
                        type="number"
                        min={1}
                        value={resizeWidth}
                        onChange={(event) => setResizeWidth(event.target.value)}
                        placeholder="e.g. 1200"
                        disabled={!selectedFile}
                      />
                    </OptionRow>
                    <OptionRow label="Height (px)">
                      <Input
                        type="number"
                        min={1}
                        value={resizeHeight}
                        onChange={(event) => setResizeHeight(event.target.value)}
                        placeholder="Auto if only width is set"
                        disabled={!selectedFile}
                      />
                    </OptionRow>
                  </div>
                  <OptionRow
                    label="Target size (KB, optional)"
                    description="Only reliably honored for JPEG output — PNG/TIFF are lossless, so this is best-effort for those."
                  >
                    <Input
                      type="number"
                      min={1}
                      value={resizeTargetSizeKb}
                      onChange={(event) => setResizeTargetSizeKb(event.target.value)}
                      placeholder="e.g. 200"
                      disabled={!selectedFile}
                    />
                  </OptionRow>
                  {!resizeParamsProvided && selectedFile && (
                    <p className="text-xs text-ink-soft">Set width, height, and/or a target size to continue.</p>
                  )}
                </div>
              )}

              {tool === "ocr" && (
                <div className="flex flex-col gap-2">
                  <p className="text-sm text-ink-soft">
                    Adds an invisible, searchable text layer to a scanned PDF. Pages that already
                    have real text are left alone unless you force it below.
                  </p>
                  <label className="flex w-fit items-center gap-2 text-sm text-ink">
                    <input
                      type="checkbox"
                      checked={forceOcr}
                      onChange={(event) => setForceOcr(event.target.checked)}
                      disabled={!selectedFile}
                      className="h-4 w-4 rounded border-line accent-ink"
                    />
                    Re-OCR every page, even ones that already have text
                  </label>
                </div>
              )}

              <div className="flex flex-col gap-2 border-t border-line pt-4">
                <Button type="button" onClick={handleRun} disabled={!isSelectionValid || run.status === "running"}>
                  {run.status === "running" ? "Running..." : `Run ${TOOLS.find((t) => t.key === tool)?.label}`}
                </Button>

                {run.status === "running" && (
                  <div className="flex flex-col gap-1">
                    <div className="h-1.5 w-full overflow-hidden rounded-full bg-sidebar-bg">
                      <div
                        className="h-full bg-ink transition-all"
                        style={{ width: `${run.progress}%` }}
                      />
                    </div>
                    <span className="text-xs tabular-nums text-ink-soft">
                      {run.progress < 100 ? `Uploading — ${run.progress}%` : "Processing on the server..."}
                    </span>
                  </div>
                )}

                {run.status === "error" && run.error && (
                  <p className="rounded-md bg-overdue/10 px-3 py-2 text-sm text-overdue">{run.error}</p>
                )}
              </div>
            </Card>

            {run.status === "done" && (
              <Card className="flex flex-col gap-3 p-5">
                <h3 className="text-sm font-semibold text-ink">
                  {run.results.length > 1 ? `${run.results.length} files ready` : "Ready"}
                </h3>
                <div className="flex flex-col gap-2">
                  {run.results.map((result) => (
                    <div
                      key={result.id}
                      className="flex items-center justify-between gap-3 rounded-md border border-line bg-paper px-3 py-2.5"
                    >
                      <div className="flex min-w-0 items-center gap-2">
                        <FileTypeIcon filename={result.output_filename} />
                        <div className="flex min-w-0 flex-col">
                          <span className="truncate text-sm text-ink">{result.output_filename}</span>
                          <span className="text-xs text-ink-soft">{formatBytes(result.size_bytes)}</span>
                        </div>
                      </div>
                      <ToolDownloadButton toolFileId={result.id} />
                    </div>
                  ))}
                </div>
                <p className="text-xs text-muted">
                  Download links expire after 5 minutes; the files themselves are kept for 24 hours.
                </p>
              </Card>
            )}
          </div>

          <EditorChatPanel
            files={files}
            knownToolFiles={knownToolFiles}
            onNewToolFiles={(newFiles) => setKnownToolFiles((prev) => [...prev, ...newFiles])}
          />
        </div>
      )}
      </>
      )}
    </div>
  );
}
