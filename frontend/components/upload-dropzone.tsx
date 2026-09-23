import { UploadCloud } from "lucide-react";
import type { ChangeEvent, DragEvent, RefObject } from "react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";

// Extracted out of app/(app)/upload/page.tsx so the landing page's "Upload"
// preview (see CLAUDE.md's Public landing page section) can render the exact
// same dropzone/progress-bar markup with `interactive={false}` and a fixed
// sample progress value, instead of a separate visual re-creation of it.

export function DropzoneBox({
  isDragging = false,
  interactive = true,
  className,
  onDragOver,
  onDragLeave,
  onDrop,
  onChooseFile,
  fileInputRef,
  onFileChange,
}: {
  isDragging?: boolean;
  interactive?: boolean;
  className?: string;
  onDragOver?: (event: DragEvent<HTMLDivElement>) => void;
  onDragLeave?: () => void;
  onDrop?: (event: DragEvent<HTMLDivElement>) => void;
  onChooseFile?: () => void;
  fileInputRef?: RefObject<HTMLInputElement>;
  onFileChange?: (event: ChangeEvent<HTMLInputElement>) => void;
}) {
  return (
    <div
      {...(interactive ? { onDragOver, onDragLeave, onDrop } : {})}
      className={cn(
        "relative flex flex-col items-center justify-center gap-3 overflow-hidden rounded-lg border-2 border-dashed bg-paper-raised px-6 py-16 text-center transition-colors",
        isDragging ? "border-filed" : "border-line",
        !interactive && "pointer-events-none",
        className
      )}
    >
      <div
        className="pointer-events-none absolute inset-0"
        style={{
          backgroundImage: "radial-gradient(circle at 50% 35%, rgba(63,102,89,0.12) 0%, transparent 55%)",
        }}
      />
      <UploadCloud className="relative z-10 h-9 w-9 text-ink-soft" strokeWidth={1.5} />
      <p className="relative z-10 text-sm text-ink">Drop a document, or browse your files</p>
      <p className="relative z-10 text-xs text-muted">PDF, JPG, PNG, TIFF — up to 20MB</p>
      <Button
        type="button"
        variant="outline"
        className="relative z-10 mt-2"
        onClick={onChooseFile}
        disabled={!interactive}
      >
        Choose file
      </Button>
      {interactive && (
        <input
          ref={fileInputRef}
          type="file"
          accept=".pdf,.jpg,.jpeg,.png,.tif,.tiff"
          className="hidden"
          onChange={onFileChange}
        />
      )}
    </div>
  );
}

export function UploadProgressCard({ progress }: { progress: number }) {
  return (
    <Card className="flex flex-col gap-2 p-5">
      <div className="h-2 w-full overflow-hidden rounded-full bg-sidebar-bg">
        <div className="h-full bg-ink transition-all" style={{ width: `${progress}%` }} />
      </div>
      <span className="text-xs tabular-nums text-ink-soft">Uploading — {progress}%</span>
    </Card>
  );
}
