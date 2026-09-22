import * as Sentry from "@sentry/nextjs";

import { scrubSensitiveData } from "./lib/sentry-scrub";

// Next.js's own server-side instrumentation hook (stable since Next 14, gated
// behind next.config.mjs's experimental.instrumentationHook for this Next 14.x
// project — Next 15+ doesn't need that flag). Sentry.init must live here, not
// in a separate sentry.server.config.ts, per @sentry/nextjs's own current
// setup — it warns at build time if it finds Sentry.init anywhere else.
export async function register() {
  const dsn = process.env.NEXT_PUBLIC_SENTRY_DSN;
  if (!dsn) return; // no-op locally / until a Sentry project exists — see CLAUDE.md

  Sentry.init({
    dsn,
    environment: process.env.NEXT_PUBLIC_SENTRY_ENVIRONMENT ?? "development",
    // Default (false): don't attach IPs/cookies/request bodies automatically —
    // matches the backend's error_tracking.py send_default_pii=False.
    sendDefaultPii: false,
    tracesSampleRate: 0,
    beforeSend: scrubSensitiveData,
  });
}

export const onRequestError = Sentry.captureRequestError;
