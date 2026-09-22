import * as Sentry from "@sentry/nextjs";

import { scrubSensitiveData } from "./lib/sentry-scrub";

// Next.js's native client instrumentation convention (the modern replacement
// for a separate sentry.client.config.ts) — @sentry/nextjs looks for this
// exact filename at build time.
const dsn = process.env.NEXT_PUBLIC_SENTRY_DSN;

if (dsn) {
  Sentry.init({
    dsn,
    environment: process.env.NEXT_PUBLIC_SENTRY_ENVIRONMENT ?? "development",
    sendDefaultPii: false,
    tracesSampleRate: 0,
    beforeSend: scrubSensitiveData,
  });
}

// Required for navigation instrumentation — @sentry/nextjs's build step warns
// if this isn't exported.
export const onRouterTransitionStart = Sentry.captureRouterTransitionStart;
