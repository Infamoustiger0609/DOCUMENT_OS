# DocumentOS AI

AI-powered document intelligence for Indian CA (Chartered Accountant) and law firms — upload contracts, invoices, GST filings, and purchase orders, and get automatic OCR, classification, structured data extraction, deadline tracking, and an AI copilot that can answer questions across your entire document registry.

This README is the entry point: what the product is, who it's for, what's actually been built, and what's still ahead. For exact implementation detail — endpoints, schemas, migrations, verification notes, every design decision and why it was made — see **[CLAUDE.md](CLAUDE.md)**, which is the authoritative technical reference this project is built and maintained against. Together, these two files are meant to give anyone (a new engineer, a stakeholder, or a fresh Claude Code session with zero prior context) the full picture of the project at any point in time: this file for *what and why*, CLAUDE.md for *how, exactly*.

## Contents

- [What problem this solves](#what-problem-this-solves)
- [Live deployments](#live-deployments)
- [How it works, in one paragraph](#how-it-works-in-one-paragraph)
- [Tech stack](#tech-stack)
- [What's implemented](#whats-implemented)
- [Repository layout](#repository-layout)
- [Running it locally](#running-it-locally)
- [Testing](#testing)
- [Current status](#current-status)
- [Known limitations](#known-limitations)
- [Future scope / roadmap](#future-scope--roadmap)
- [Where to look for more detail](#where-to-look-for-more-detail)

## What problem this solves

Indian CA and law firms deal with a constant stream of documents — client agreements, vendor invoices, GST filings and returns, purchase orders — each with its own deadlines, parties, and compliance obligations. Today that mostly means manually reading each one, tracking renewal/due/filing dates in a spreadsheet, and re-opening files whenever someone needs to check a number or a clause.

DocumentOS AI automates the reading and tracking: upload a document, and it's OCR'd, classified, and has its key fields (parties, amounts, GSTINs, dates) extracted automatically. Every deadline lands on one sorted registry instead of a spreadsheet. An AI copilot can answer plain-English questions across the whole registry ("which invoices are overdue", "find contracts mentioning exclusivity") instead of someone opening every file by hand. It's specifically built around Indian compliance — GSTIN/PAN checksum validation, lakh/crore-aware amount parsing, CGST/SGST/IGST classification, GST-specific filing deadlines — rather than being a generic Western SMB document tool with a rupee symbol added on.

## Live deployments

- **Frontend (Render)**: https://documentos-ai-frontend.onrender.com
- **Frontend (Vercel)**: https://document-os-ai.vercel.app — a second, independent deployment of the same frontend, both currently pointing at the same backend. See CLAUDE.md's [Second frontend deployment (Vercel)](CLAUDE.md#second-frontend-deployment-vercel) section for why two exist and what has to stay in sync between them.
- **Backend (Render)**: a Docker web service — its real hostname has a random suffix (the plain name collided with an unrelated existing Render service), so don't guess it; check the frontend's own configured `NEXT_PUBLIC_API_URL` or the Render dashboard.
- **Repository**: https://github.com/Infamoustiger0609/DOCUMENT_OS

Both frontend deployments auto-deploy on every push to `main`; the backend deploys the same way via Render's git integration, running database migrations automatically on every container start (see [Current status](#current-status)).

## How it works, in one paragraph

A user uploads a file (PDF, JPG, PNG, or TIFF) through the web app. The backend stores it in private Supabase Storage, then — in the background, not blocking the upload response — extracts its text (PyMuPDF for native PDF text, Tesseract OCR as a fallback for scans), classifies it into one of seven categories via a Groq-hosted LLM, and (for five of those categories) extracts a structured JSON of fields specific to that category, mapping one field to a tracked deadline date. Everything lands in a Postgres (Supabase) `documents` table scoped to the uploading user. From there, the frontend surfaces the registry, deadlines, and extracted fields; a persistent AI Copilot answers questions across the whole registry using a mix of real database queries and Postgres full-text search; and a separate set of tools lets a user merge/split/compress/convert files, learn a reusable template from a sample document and generate new ones from it, and apply a saved signature image to a PDF.

## Tech stack

| Layer | Choice |
|---|---|
| Backend | FastAPI (Python) |
| Frontend | Next.js 14 (App Router, TypeScript, Tailwind CSS) |
| Database | Supabase Postgres (pgvector-enabled, currently unused — see [Future scope](#future-scope--roadmap)) |
| File storage | Supabase Storage (private bucket, signed URLs only) |
| Text extraction | PyMuPDF (native PDF text) + Tesseract (OCR fallback) |
| AI inference | Groq API (`openai/gpt-oss-120b`) — classification, structured extraction, chat/copilot, analytics, template learning/generation |
| Migrations | Alembic |
| Deployment | Render (backend: Docker web service; frontend: Node web service) + a second, independent Vercel deployment of the frontend |
| Error tracking | Sentry (`sentry-sdk[fastapi]` / `@sentry/nextjs`) — wired but not yet activated, see [Known limitations](#known-limitations) |
| CI | GitHub Actions (checks only — pytest, `tsc`, lint, build, Playwright e2e) |

## What's implemented

Everything below is real, working code with test coverage — not a plan. Grouped by area; see CLAUDE.md for the exact endpoints, schemas, and design decisions behind each.

**Core document pipeline**
- Auth (JWT, bcrypt, per-user data isolation) and a real registration/login flow.
- Upload (PDF/JPG/PNG/TIFF, 20MB cap, magic-byte content validation) with the extract → classify → structure pipeline running as a background task, polled live by the upload page.
- Eight document categories (Agreement, Invoice, GST Document, GST Filing, Bank Statement, Purchase Order, Other, Data File), five of which get full structured field extraction with line-item/tax-breakdown tables.
- **Data Files**: upload an Excel (`.xlsx`) spreadsheet and it's handled by a completely separate path — no classification/structured extraction, just deterministic per-sheet metadata (columns, row counts) — and its actual rows are then queryable with real, sandboxed SQL (via an in-memory DuckDB connection) from both the per-document chat and the registry-wide Copilot, plus a dedicated merge tool (combine as separate sheets, or concatenate rows into one).
- India-specific intelligence: real GSTIN/PAN checksum validation (not just format checks), lakh/crore-aware number parsing, automatic CGST/SGST/IGST derivation and intra-/inter-state classification, GST-specific filing deadlines as their own category.
- Document registry (`/documents`) with server-side category/date filtering, and a dedicated Deadlines view (`/tasks`) bucketed by urgency.
- Per-document AI chat (ask questions about one document's text) and a registry-wide **AI Copilot** (structured queries against real DB fields + Postgres full-text search across raw text, with honest "keyword search, not semantic" framing).
- **Analytics**: genuinely dynamic, AI-selected metrics computed from whatever data actually exists (no hardcoded metric list) — with hard code-level guarantees that every number shown was actually computed from real rows, never fabricated by the model, and that every category with data gets at least one metric.

**Document tools & generation**
- A document-editing **Workspace**: merge/split/compress/convert/resize/OCR-to-searchable-PDF (plus a dedicated Excel merge mode — combine as separate sheets, or concatenate rows), both via a manual tool picker and a natural-language chat assistant that chains tool calls.
- **Document generation**: learn a reusable field-structure template from one sample document, then generate new documents (filled via a form or a plain-language instruction) as professionally laid-out PDFs — with a token-based field classifier that adapts the layout to whatever fields a given template actually has (invoice-shaped, lease-shaped, NDA-shaped, etc.), not a fixed invoice template.
- **Document source separation**: template-learning samples and generated documents are marked and kept out of the main registry, surfaced instead in their own "Uploaded Templates"/"Generated Documents" sections in the Workspace.
- **E-signature**: save one signature image, stamp it onto any owned PDF (self-serve only — explicitly not a multi-party signature-request workflow; see [Known limitations](#known-limitations)).

**Security, ops, and quality**
- A full security audit (path traversal, dependency CVEs, rate limiting gaps, missing startup validation, audit logging) with every critical/high finding fixed and verified live against the real deployment — see CLAUDE.md's [Security audit](CLAUDE.md#security-audit) section.
- Rate limiting, structured JSON logging with per-request IDs, an audit log of who accessed/downloaded/deleted/signed/generated which document, in-process TTL caching, and a background retention sweep for transient tool outputs.
- **Migrate-on-boot**: database migrations run automatically on every deploy (fixed after a real incident where a shipped migration wasn't applied to production — see CLAUDE.md's [Deployment](CLAUDE.md#deployment-render) section).
- Two real test suites: a backend pytest suite (225+ tests, run against a disposable Postgres, never the real Supabase project) and a Playwright e2e suite for the frontend — both wired into CI on every PR.
- A public, unauthenticated marketing landing page at `/` (the authenticated dashboard lives at `/home`), whose feature previews are literally the app's real dashboard components rendered with sample data — not screenshots — so they can't visually drift out of sync with the real app.

## Repository layout

```
DOCUMENT OS/
├── backend/            FastAPI app, Alembic migrations, pytest suite
├── frontend/            Next.js app (App Router), Playwright e2e suite
├── render.yaml           Render Blueprint (both services)
├── CLAUDE.md             Full technical reference — read this for implementation detail
└── README.md            This file
```

## Running it locally

Full setup steps (env vars, system dependencies like Tesseract/Ghostscript/LibreOffice, manual vs. one-command startup) are in CLAUDE.md's [Running locally](CLAUDE.md#running-locally) and [Environment variables](CLAUDE.md#environment-variables) sections. The short version:

```powershell
# from the repo root, after backend/venv and frontend/node_modules exist
.\dev.ps1
```
```bash
./dev.sh   # macOS/Linux
```

Backend: `http://localhost:8000` (health check at `/health`). Frontend: `http://localhost:3000`.

## Testing

```bash
.\test.ps1   # Windows
./test.sh    # macOS/Linux/git-bash
```

Runs the full backend pytest suite against a disposable Postgres container, then the frontend Playwright suite, stopping on first failure. See CLAUDE.md's [Testing](CLAUDE.md#testing) section for the full breakdown of what each suite covers, and its [Testing policy](CLAUDE.md#testing-policy) subsection for how tests should be run while iterating (targeted runs, not the full suite after every edit).

## Current status

This has gone well past a proof-of-concept. Real hardening work has been done and verified live against the actual deployment, not just written and assumed: per-user data isolation, a full security audit with fixes, rate limiting, structured logging, an audit trail, and automatic database migrations on deploy. CI runs the full test suite on every PR (branch protection to actually require it passing before merge is a manual GitHub setting still worth turning on — see [Known limitations](#known-limitations)).

That said, this is still a single-tenant-per-account, single-process deployment on Render's free tier, using two independent frontend deployments that happen to share one backend. It has not yet been used with a real client's confidential data, and there's a concrete compliance prerequisite (see below) before it should be.

## Known limitations

- **JWT stored in `localStorage`, not an httpOnly cookie.** A deliberate POC-era trade-off, re-assessed (not changed) during the security audit — the calculus is different now that real financial/identity data could flow through this app. A migration to httpOnly cookies is a well-scoped follow-up, not yet done.
- **Next.js is on 14.2.15**, which has known CVEs (checked for applicability, one stopgapped via `images.unoptimized`) — the real fix is a major-version upgrade (14 → 15/16), deliberately not attempted inside the security audit given the regression-testing surface (App Router behavior, Sentry instrumentation).
- **No field-level encryption** for GSTIN/PAN/bank details inside `extracted_json`, beyond Supabase's disk-level encryption at rest. Real column-level encryption would need a KMS story and would break both Copilot's full-text search and Analytics' aggregation as currently designed.
- **Semantic search doesn't exist yet.** The Copilot's content search is real Postgres full-text (keyword) search — it says so honestly rather than implying it understands meaning. `pgvector` has been enabled in the database since the very first migration specifically for this, unused so far.
- **E-signature is self-serve only** — a user signs their own document with their own saved signature. There is no multi-party "send this to someone else to sign and track status" workflow, and no cryptographic/PAdES-style tamper-evident signing; it's a visual stamp, not a legally-certified e-signature.
- **Document generation doesn't clone visual layout** — it reproduces the same fields/sections/structure as the sample, not the sample's exact fonts, logo placement, or spacing.
- **A real compliance prerequisite, not yet met**: before any real firm's client data goes into this app, it needs a lawyer-reviewed privacy policy, consent flows, and the other DPDP Act 2023 / DPDP Rules 2025 safeguards (see CLAUDE.md's [Security audit](CLAUDE.md#security-audit) research findings) — flagged as a concrete blocker, not a hypothetical one.
- **Sentry is wired up but not activated** — no DSN is configured yet, so error tracking currently no-ops on both frontend and backend. Creating the actual Sentry project is a manual, interactive step.
- **Uptime monitoring isn't set up yet** (recommended: UptimeRobot pinging `/health` every 5 minutes) — also a manual, interactive step, and one that incidentally keeps Render's free-tier backend from cold-starting during business hours.
- **Single-process architecture** — in-process caching, rate-limit storage, and background task execution all assume one backend process. This is fine at current scale and stops being fine the moment this runs with more than one instance/worker.

## Future scope / roadmap

Ordered roughly by how self-contained each item is, not by priority — pick based on what actually matters next.

**Near-term, well-scoped**
- Turn on GitHub branch protection for `main` (require the 3 CI jobs to pass before merge) — a manual dashboard setting, not code.
- Create the actual Sentry project and wire in the two DSNs; set up UptimeRobot.
- A real "log out all other sessions" Settings button — the `token_version` mechanism already exists for this, it just needs a button and an endpoint.
- Extend Analytics' currency handling beyond an unconditional INR default, if a genuinely non-Indian document ever enters a real registry.
- Fix the known bug where a template-generated document's placeholder `extracted_json` shape (`_generated`/`_field_schema`/`_values`) pollutes Analytics' per-category field list — identified, not yet fixed.
- Decide whether Deadlines/Analytics/Copilot should also exclude template-generated documents the way the main registry now does (an explicit open decision, not yet made either way).

**Medium-term, real engineering effort**
- Real semantic/embedding-based search for the Copilot, using the `pgvector` extension that's been sitting enabled and unused since the first migration.
- The httpOnly-cookie migration for JWT storage.
- The Next.js 14 → 15/16 major-version upgrade, with full regression testing (especially the Sentry instrumentation, which is genuinely version-14-specific today).
- A real task queue (Celery + Redis, or `arq`/RQ) once this needs to survive a process restart mid-task, or scales past what `BackgroundTasks` on a single process can handle.
- Redis for caching, rate-limit storage, and shared state generally, the moment this runs as more than one backend process/instance.

**Larger, new-phase-sized work**
- A multi-party e-signature workflow (send a document to someone else to sign, track status, real identity verification) — a substantially different, bigger feature than today's self-serve signing, with real legal-validity questions (eIDAS/ESIGN-style) to work through.
- Visual-layout cloning for document generation (pixel-identical to the original sample) rather than just matching field structure.
- The DPDP Act 2023/Rules 2025 compliance work itself (privacy notice, consent, data-principal rights, breach notification process) — a prerequisite for any real client data, involving legal review, not just code.
- Field-level encryption for sensitive extracted fields, if this ever needs to scale beyond a single tenant's data to many firms' data at real volume.

## Where to look for more detail

- **CLAUDE.md** is the full technical reference — every endpoint, database column, migration, design decision, and verification note. If you need to know *exactly* how something works or *why* a specific choice was made, that file has it; this README deliberately doesn't duplicate that level of detail so the two don't drift out of sync with each other.
- Both files are meant to be kept current as the project evolves — update this README's [What's implemented](#whats-implemented)/[Future scope](#future-scope--roadmap) sections and CLAUDE.md's own relevant section together whenever a real feature ships or a roadmap item gets picked up.
