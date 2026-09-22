"use client";

import { PanelRightClose, Send, Sparkles } from "lucide-react";
import Link from "next/link";
import { type FormEvent, useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useAuth } from "@/lib/auth-context";
import { cn } from "@/lib/utils";

// Persistent, global — mounted once in app/(app)/layout.tsx (not per-page),
// so its conversation survives client-side navigation between /documents,
// /workspace, /documents/[id], etc., the same way AuthProvider in the root
// layout survives navigation (see CLAUDE.md's Authentication section). This
// is deliberately a separate feature from documents/[id]'s per-document
// ChatCard: that one answers questions about ONE document's raw_text; this
// one answers questions about the whole registry (see CLAUDE.md's Copilot
// section) and has no idea what page you're currently on.
const COLLAPSED_STORAGE_KEY = "documentos_copilot_collapsed";

interface CopilotCitation {
  id: string;
  filename: string;
  category: string | null;
  status: string;
  deadline_date: string | null;
}

interface CopilotMessage {
  role: "user" | "assistant";
  content: string;
  citations?: CopilotCitation[];
}

function readStoredCollapsed(): boolean {
  try {
    return window.localStorage.getItem(COLLAPSED_STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

function writeStoredCollapsed(collapsed: boolean) {
  try {
    window.localStorage.setItem(COLLAPSED_STORAGE_KEY, collapsed ? "1" : "0");
  } catch {
    // ignore — e.g. private-browsing storage restrictions
  }
}

function CitationRow({ citation }: { citation: CopilotCitation }) {
  return (
    <Link
      href={`/documents/${citation.id}`}
      className="flex items-center justify-between gap-2 rounded-md border border-line bg-paper px-2.5 py-2 text-xs transition-colors hover:bg-sidebar-bg"
    >
      <span className="min-w-0 truncate text-ink">{citation.filename}</span>
      <span className="shrink-0 text-ink-soft">{citation.category ?? "Uncategorized"}</span>
    </Link>
  );
}

export function CopilotPanel() {
  const { authFetch } = useAuth();
  const [collapsed, setCollapsed] = useState(true);
  const [hydrated, setHydrated] = useState(false);
  const [messages, setMessages] = useState<CopilotMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Read the persisted open/closed preference only after mount (avoids a
  // server/client render mismatch — localStorage doesn't exist during SSR).
  useEffect(() => {
    setCollapsed(readStoredCollapsed());
    setHydrated(true);
  }, []);

  const toggleCollapsed = useCallback(() => {
    setCollapsed((prev) => {
      const next = !prev;
      writeStoredCollapsed(next);
      return next;
    });
  }, []);

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

      try {
        const res = await authFetch("/copilot/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message: trimmed, history }),
        });
        if (!res.ok) {
          const data = await res.json().catch(() => null);
          throw new Error(data?.detail ?? "Could not get a response.");
        }
        const data: { reply: string; citations: CopilotCitation[] } = await res.json();
        setMessages((prev) => [...prev, { role: "assistant", content: data.reply, citations: data.citations }]);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Could not get a response.");
      } finally {
        setSending(false);
      }
    },
    [input, sending, messages, authFetch]
  );

  // Nothing meaningful to render before hydration settles — a 1px-wide
  // sliver rather than a flash of the wrong (server-guessed) width.
  if (!hydrated) {
    return <aside className="w-0 shrink-0" />;
  }

  if (collapsed) {
    return (
      <aside className="sticky top-0 flex h-screen w-12 shrink-0 flex-col items-center border-l border-line bg-paper-raised py-4">
        <button
          type="button"
          onClick={toggleCollapsed}
          aria-label="Open DocumentOS Copilot"
          title="Open DocumentOS Copilot"
          className="rounded-md p-2 text-ink-soft transition-colors hover:bg-sidebar-bg hover:text-ink"
        >
          <Sparkles className="h-4 w-4" strokeWidth={1.75} />
        </button>
      </aside>
    );
  }

  return (
    <aside className="sticky top-0 flex h-screen w-[380px] shrink-0 flex-col border-l border-line bg-paper-raised">
      <div className="flex items-center justify-between gap-2 border-b border-line px-4 py-3.5">
        <div className="flex items-center gap-2">
          <Sparkles className="h-4 w-4 text-ink" strokeWidth={1.75} />
          <span className="text-sm font-semibold text-ink">DocumentOS Copilot</span>
        </div>
        <button
          type="button"
          onClick={toggleCollapsed}
          aria-label="Collapse Copilot"
          title="Collapse"
          className="rounded-md p-1.5 text-ink-soft transition-colors hover:bg-sidebar-bg hover:text-ink"
        >
          <PanelRightClose className="h-4 w-4" strokeWidth={1.75} />
        </button>
      </div>

      <div className="flex flex-1 flex-col gap-3 overflow-y-auto px-4 py-4">
        {messages.length === 0 ? (
          <div className="flex flex-col gap-2 text-sm text-ink-soft">
            <p>Ask about your registry — deadlines, categories, or what&apos;s in a document&apos;s text.</p>
            <p className="text-xs text-muted">
              e.g. &ldquo;show all active agreements&rdquo;, &ldquo;which invoices are
              overdue&rdquo;, &ldquo;find contracts mentioning exclusivity&rdquo;
            </p>
          </div>
        ) : (
          messages.map((message, index) => (
            <div
              key={index}
              className={cn("flex flex-col gap-1.5", message.role === "user" ? "items-end" : "items-start")}
            >
              <div
                className={cn(
                  "max-w-[95%] whitespace-pre-wrap rounded-xl px-3.5 py-2.5 text-sm leading-relaxed",
                  message.role === "user"
                    ? "rounded-br-sm bg-ink text-paper"
                    : "rounded-bl-sm bg-sidebar-bg text-ink"
                )}
              >
                {message.content}
              </div>
              {message.citations && message.citations.length > 0 && (
                <div className="flex w-[95%] flex-col gap-1.5">
                  {message.citations.map((citation) => (
                    <CitationRow key={citation.id} citation={citation} />
                  ))}
                </div>
              )}
            </div>
          ))
        )}
        {sending && (
          <div className="self-start rounded-xl rounded-bl-sm bg-sidebar-bg px-3.5 py-2.5 text-sm text-ink-soft">
            Looking into it...
          </div>
        )}
      </div>

      {error && <p className="px-4 pb-2 text-xs text-overdue">{error}</p>}

      <form onSubmit={handleSend} className="flex items-center gap-2 border-t border-line p-3.5">
        <Input
          type="text"
          placeholder="Ask DocumentOS..."
          value={input}
          onChange={(event) => setInput(event.target.value)}
          disabled={sending}
          className="flex-1"
        />
        <Button type="submit" size="icon" disabled={sending || !input.trim()}>
          <Send className="h-4 w-4" strokeWidth={1.75} />
        </Button>
      </form>
    </aside>
  );
}
