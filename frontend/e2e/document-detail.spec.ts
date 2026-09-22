import { expect, test } from "@playwright/test";

import { mockAuthedSession, mockDocumentDetail, mockDocumentsList, mockSignedVersions } from "./helpers";

const DOCUMENT_ID = "33333333-3333-3333-3333-333333333333";

const LIST_ROW = {
  id: DOCUMENT_ID,
  filename: "Acme_Invoice_2026.pdf",
  category: "Invoice",
  upload_date: "2026-01-01T00:00:00Z",
  storage_path: `${DOCUMENT_ID}.pdf`,
  extracted_json: { vendor_name: "Acme Corp" },
  deadline_date: "2026-12-01",
  status: "processed",
  error_message: null,
  classification_reasoning: "Has an invoice number and vendor.",
  classification_version: 2,
};

const DETAIL = {
  ...LIST_ROW,
  raw_text: "Invoice #123 from Acme Corp, due 2026-12-01, total $500.",
  extracted_json: {
    vendor_name: "Acme Corp",
    invoice_number: "123",
    invoice_date: "2026-11-01",
    due_date: "2026-12-01",
    amount: 500,
    gst_number: null,
  },
};

test("navigates from the documents row to the document detail page", async ({ page }) => {
  await mockAuthedSession(page);
  await mockDocumentsList(page, [LIST_ROW]);
  await mockDocumentDetail(page, DOCUMENT_ID, DETAIL);
  await mockSignedVersions(page, DOCUMENT_ID);

  await page.goto("/documents");
  await page.getByText("Acme_Invoice_2026.pdf").click();

  await expect(page).toHaveURL(`/documents/${DOCUMENT_ID}`);
  await expect(page.getByRole("heading", { name: "Acme_Invoice_2026.pdf" })).toBeVisible();
  await expect(page.getByText("Acme Corp")).toBeVisible();
});
