import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

// Extracted out of copilot-panel.tsx so the landing page's "Ask Copilot"
// preview (see CLAUDE.md's Public landing page section) can render a real
// sample exchange using the exact same bubble styling the Copilot panel (and
// documents/[id]'s ChatCard, which shares this same visual pattern) uses.
export function ChatBubble({
  role,
  content,
  children,
}: {
  role: "user" | "assistant";
  content: string;
  children?: ReactNode;
}) {
  return (
    <div className={cn("flex flex-col gap-1.5", role === "user" ? "items-end" : "items-start")}>
      <div
        className={cn(
          "max-w-[95%] whitespace-pre-wrap rounded-xl px-3.5 py-2.5 text-sm leading-relaxed",
          role === "user" ? "rounded-br-sm bg-ink text-paper" : "rounded-bl-sm bg-sidebar-bg text-ink"
        )}
      >
        {content}
      </div>
      {children}
    </div>
  );
}
