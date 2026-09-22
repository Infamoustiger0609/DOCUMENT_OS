import type { Page, Route } from "@playwright/test";

// These tests never hit a live backend — every call is intercepted with
// page.route() and fulfilled with a fixed JSON response. See the "Testing"
// section in CLAUDE.md for why (keeps this suite fast, hermetic, and
// independent of the pytest test database). API_URL/TOKEN_STORAGE_KEY must
// match auth-context.tsx's own constants exactly, or nothing here matches
// what the app actually requests/reads.
export const API_URL = "http://localhost:8000";
const TOKEN_STORAGE_KEY = "documentos_token";

export const FAKE_USER = {
  id: "11111111-1111-1111-1111-111111111111",
  email: "e2e@example.com",
  name: "E2E Tester",
  role: "user",
  created_at: "2026-01-01T00:00:00Z",
  due_soon_threshold_days: 30,
  has_signature: false,
};

function json(route: Route, status: number, body: unknown) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

// Matching by url.pathname (rather than a glob string) sidesteps two real
// gotchas: query strings on GET /documents make literal-string routes miss,
// and a loose glob like "**/documents**" would also swallow
// /documents/deadlines, /documents/{id}, and /documents/upload. Exported for
// tests that need a custom (e.g. stateful, call-counting) handler beyond what
// the fixed-response helpers below cover.
export function forPath(pathname: string) {
  return (url: URL) => url.origin === API_URL && url.pathname === pathname;
}

/** Mocks GET /auth/me alone — used both by mockAuthedSession (below) and by the
 * login flow itself, which calls it right after a successful POST /auth/login to
 * verify the new token before storing anything (see auth-context.tsx's login()). */
export async function mockCurrentUser(page: Page) {
  await page.route(forPath("/auth/me"), (route) => json(route, 200, FAKE_USER));
}

/** Seeds localStorage with a token and mocks GET /auth/me, so AuthGuard lets a
 * page in the (app) route group render as if already logged in — for tests that
 * start past the login screen (upload, document detail, ...). */
export async function mockAuthedSession(page: Page) {
  await page.addInitScript(
    ({ key, token }) => window.localStorage.setItem(key, token),
    { key: TOKEN_STORAGE_KEY, token: "fake-e2e-token" }
  );
  await mockCurrentUser(page);
}

export async function mockDocumentsList(page: Page, documents: unknown[]) {
  await page.route(forPath("/documents"), (route) => json(route, 200, documents));
}

export async function mockDocumentDetail(page: Page, id: string, document: unknown) {
  await page.route(forPath(`/documents/${id}`), (route) => json(route, 200, document));
}

export async function mockUpload(page: Page, body: unknown, status = 200) {
  await page.route(forPath("/documents/upload"), (route) => json(route, status, body));
}

export async function mockLogin(page: Page, status: number, body: unknown) {
  await page.route(forPath("/auth/login"), (route) => json(route, status, body));
}

export async function mockProfileUpdate(page: Page, body: unknown, status = 200) {
  await page.route(forPath("/auth/profile"), (route) => json(route, status, body));
}

// Phase 31 — see CLAUDE.md's E-signature section. Settings' Signature card
// and the document detail page's Signature card both fetch these on mount.
export async function mockSignatureStatus(
  page: Page,
  body: { has_signature: boolean; download_url?: string | null; expires_in?: number | null } = {
    has_signature: false,
  }
) {
  await page.route(forPath("/auth/signature"), (route) => json(route, 200, body));
}

export async function mockSignedVersions(page: Page, documentId: string, versions: unknown[] = []) {
  await page.route(forPath(`/documents/${documentId}/signed`), (route) => json(route, 200, versions));
}
