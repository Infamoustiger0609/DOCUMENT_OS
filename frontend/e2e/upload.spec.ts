import path from "path";

import { expect, test } from "@playwright/test";

import { mockAuthedSession, mockDocumentDetail, mockUpload } from "./helpers";

const SAMPLE_PDF = path.join(__dirname, "fixtures", "sample.pdf");
const DOCUMENT_ID = "22222222-2222-2222-2222-222222222222";

test("uploads a file, shows it processing, then polls through to the final status", async ({ page }) => {
  await mockAuthedSession(page);
  // POST /documents/upload now returns immediately, before the pipeline runs
  // (see CLAUDE.md's Background processing section) — the response is always
  // just the "uploaded" state, never the final one.
  await mockUpload(page, {
    id: DOCUMENT_ID,
    status: "uploaded",
    category: null,
    raw_text: null,
    extracted_json: null,
    deadline_date: null,
    error_message: null,
    classification_reasoning: null,
    classification_version: null,
  });
  // The frontend discovers the real outcome by polling GET /documents/{id}.
  await mockDocumentDetail(page, DOCUMENT_ID, {
    id: DOCUMENT_ID,
    filename: "sample.pdf",
    status: "processed",
    category: "Invoice",
    raw_text: "Invoice #123 from Acme Corp.",
    extracted_json: { vendor_name: "Acme Corp" },
    deadline_date: "2026-12-01",
    error_message: null,
    classification_reasoning: "Has an invoice number and vendor.",
    classification_version: 2,
  });

  await page.goto("/upload");
  await page.locator('input[type="file"]').setInputFiles(SAMPLE_PDF);

  await expect(page.getByText("Still working — this updates automatically.")).toBeVisible();
  await expect(page.getByText("Classified as Invoice")).toBeVisible();
});

test("shows the backend's error message when upload validation fails", async ({ page }) => {
  await mockAuthedSession(page);
  await mockUpload(
    page,
    {
      detail: "File content doesn't match its extension. Please upload a genuine PDF, JPG, PNG, or TIFF file.",
    },
    400
  );

  await page.goto("/upload");
  await page.locator('input[type="file"]').setInputFiles(SAMPLE_PDF);

  await expect(
    page.getByText("File content doesn't match its extension. Please upload a genuine PDF, JPG, PNG, or TIFF file.")
  ).toBeVisible();
});
