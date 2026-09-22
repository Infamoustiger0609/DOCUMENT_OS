"use client";

import * as Sentry from "@sentry/nextjs";
import { useEffect } from "react";

// Next.js's special case: this only renders when an error escapes the root
// layout itself (regular page-level errors use a normal error.tsx instead,
// which this app doesn't have yet — nothing has needed one). Because it
// replaces the root layout, it has to render its own <html>/<body>.
export default function GlobalError({ error }: { error: Error & { digest?: string } }) {
  useEffect(() => {
    Sentry.captureException(error);
  }, [error]);

  return (
    <html lang="en">
      <body className="flex min-h-screen items-center justify-center bg-paper font-sans text-ink antialiased">
        <div className="flex max-w-sm flex-col items-center gap-3 px-6 text-center">
          <h1 className="font-serif text-xl font-semibold">Something went wrong</h1>
          <p className="text-sm text-ink-soft">
            An unexpected error occurred. It&apos;s been reported automatically — try reloading
            the page.
          </p>
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="mt-2 rounded-md bg-ink px-4 py-2 text-sm text-paper hover:opacity-90"
          >
            Reload
          </button>
        </div>
      </body>
    </html>
  );
}
