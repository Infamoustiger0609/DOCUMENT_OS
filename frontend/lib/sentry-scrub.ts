import type * as Sentry from "@sentry/nextjs";

const SENSITIVE_KEYS = new Set([
  "password",
  "current_password",
  "new_password",
  "authorization",
  "access_token",
  "token",
  "raw_text",
]);

function scrub(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(scrub);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>).map(([key, val]) => [
        key,
        SENSITIVE_KEYS.has(key.toLowerCase()) ? "[Filtered]" : scrub(val),
      ])
    );
  }
  return value;
}

// Defense in depth on top of sendDefaultPii: false and never passing these
// fields into Sentry context in the first place (see CLAUDE.md's Security
// section) — scrubs request headers/data and any extra context one more time
// before an event ever leaves the browser/server. Shared by instrumentation.ts
// (server) and instrumentation-client.ts (browser).
export function scrubSensitiveData(
  event: Sentry.ErrorEvent,
  _hint: Sentry.EventHint
): Sentry.ErrorEvent {
  if (event.request) {
    if (event.request.headers) {
      const headers = { ...event.request.headers };
      for (const key of Object.keys(headers)) {
        if (key.toLowerCase() === "authorization") headers[key] = "[Filtered]";
      }
      event.request.headers = headers;
    }
    if (event.request.data) {
      event.request.data = scrub(event.request.data) as typeof event.request.data;
    }
  }
  if (event.extra) {
    event.extra = scrub(event.extra) as typeof event.extra;
  }
  return event;
}
