import { expect, test } from "@playwright/test";

import { mockCurrentUser, mockDocumentsList, mockLogin } from "./helpers";

test("logs in with valid credentials and redirects to home", async ({ page }) => {
  await mockLogin(page, 200, { access_token: "fake-token-from-login", token_type: "bearer" });
  await mockCurrentUser(page);
  await mockDocumentsList(page, []);

  await page.goto("/login");
  await page.getByLabel("Work email").fill("e2e@example.com");
  await page.getByLabel("Password").fill("testpass123");
  await page.getByRole("button", { name: "Log in" }).click();

  await expect(page).toHaveURL("/home");
});

test("shows the backend's error message on invalid credentials", async ({ page }) => {
  await mockLogin(page, 401, { detail: "Incorrect email or password." });

  await page.goto("/login");
  await page.getByLabel("Work email").fill("e2e@example.com");
  await page.getByLabel("Password").fill("wrongpassword");
  await page.getByRole("button", { name: "Log in" }).click();

  await expect(page.getByText("Incorrect email or password.")).toBeVisible();
  await expect(page).toHaveURL("/login");
});
