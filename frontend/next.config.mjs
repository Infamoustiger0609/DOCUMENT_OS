import { withSentryConfig } from "@sentry/nextjs/config";

/** @type {import('next').NextConfig} */
const nextConfig = {
  // Next.js 14 needs this explicitly set (stable/default-on from Next 15
  // onward) for instrumentation.ts's register()/onRequestError hooks — see
  // instrumentation.ts and CLAUDE.md's Sentry setup notes.
  experimental: {
    instrumentationHook: true,
  },
  // Security audit mitigation (see CLAUDE.md's Security section): the
  // currently-pinned Next.js 14.2.15 has a known unauthenticated RCE in its
  // built-in Image Optimization API (GHSA-2xp9-vwfh-vxw4) — the framework's
  // /_next/image route exists regardless of whether app code uses
  // next/image (it doesn't, anywhere in this app), so disabling the
  // optimizer outright closes that surface with zero functional loss, as an
  // immediate stopgap ahead of the real fix (upgrading Next.js — a bigger,
  // separately-tracked task; see CLAUDE.md).
  images: {
    unoptimized: true,
  },
};

export default withSentryConfig(nextConfig, {
  silent: true,
  // Source map upload (needs SENTRY_ORG/SENTRY_PROJECT/SENTRY_AUTH_TOKEN) is
  // intentionally not configured — see CLAUDE.md's Sentry setup section for
  // why that's a deliberate deferral, not an oversight.
  treeshake: {
    removeDebugLogging: true,
  },
});
