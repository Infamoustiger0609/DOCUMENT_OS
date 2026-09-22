import { expect, test } from "@playwright/test";

import {
  FAKE_USER,
  forPath,
  mockAuthedSession,
  mockDocumentsList,
  mockProfileUpdate,
  mockSignatureStatus,
} from "./helpers";

test("clicking the sidebar account chip navigates to Settings", async ({ page }) => {
  await mockAuthedSession(page);
  await mockDocumentsList(page, []);
  await mockSignatureStatus(page);

  await page.goto("/documents");
  await page.getByRole("link", { name: new RegExp(FAKE_USER.name) }).click();

  await expect(page).toHaveURL("/settings");
  await expect(page.getByRole("heading", { name: "Settings" })).toBeVisible();
});

test("Account tab is shown by default, and App tab shows the due-soon threshold", async ({ page }) => {
  await mockAuthedSession(page);
  await mockSignatureStatus(page);

  await page.goto("/settings");

  await expect(page.getByRole("heading", { name: "Profile" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Change password" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Danger zone" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Due soon threshold" })).not.toBeVisible();

  await page.getByRole("button", { name: "App" }).click();

  await expect(page.getByRole("heading", { name: "Due soon threshold" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Profile" })).not.toBeVisible();
});

test("updating the profile saves and refreshes the signed-in user everywhere, including the sidebar", async ({
  page,
}) => {
  await mockAuthedSession(page);
  await mockDocumentsList(page, []);
  await mockSignatureStatus(page);

  const updatedUser = { ...FAKE_USER, name: "Updated Tester" };
  await mockProfileUpdate(page, updatedUser);

  // The first GET /auth/me (on page load) must still return the original
  // user; only the one refreshUser() fires *after* a successful save should
  // return the updated name — a single static mock can't express that.
  let authMeCalls = 0;
  await page.route(forPath("/auth/me"), (route) => {
    authMeCalls += 1;
    const user = authMeCalls === 1 ? FAKE_USER : updatedUser;
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(user) });
  });

  await page.goto("/settings");

  await expect(page.getByLabel("Name")).toHaveValue(FAKE_USER.name);

  await page.getByLabel("Name").fill("Updated Tester");
  await page.getByRole("button", { name: "Save changes" }).click();

  await expect(page.getByText("Profile updated.")).toBeVisible();

  await page.goto("/documents");
  await expect(page.getByRole("link", { name: /Updated Tester/ })).toBeVisible();
});
