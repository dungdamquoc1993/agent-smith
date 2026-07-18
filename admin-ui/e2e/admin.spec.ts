import { expect, test, type Route } from "@playwright/test"

const providerId = "11111111-1111-4111-8111-111111111111"
const operator = { id: "22222222-2222-4222-8222-222222222222", username: "admin", displayName: "Admin User", status: "active" }
const session = { operator, session: { idleExpiresAt: "2026-07-19T00:00:00Z", absoluteExpiresAt: "2026-07-25T00:00:00Z" } }

async function json(route: Route, body: unknown, status = 200) {
  await route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) })
}

test("sign in opens the provider list", async ({ page }) => {
  let authenticated = false
  await page.route("**/auth/session", (route) => json(route, authenticated ? session : { error: { code: "admin_session_required", message: "Authentication required." } }, authenticated ? 200 : 401))
  await page.route("**/auth/sign-in", async (route) => { authenticated = true; await json(route, session) })
  await page.route("**/api/identity-providers?**", (route) => json(route, { identityProviders: [{ id: providerId, slug: "hris-sandbox", issuer: "hris", displayName: "HRIS Sandbox", status: "active", metadata: {}, createdAt: "2026-07-18T00:00:00Z", updatedAt: "2026-07-18T00:00:00Z" }], nextCursor: null }))

  await page.goto("/sign-in")
  await page.getByLabel("Username").fill("admin")
  await page.getByLabel("Password").fill("correct horse")
  await page.getByRole("button", { name: "Sign in" }).click()
  await expect(page.getByRole("heading", { name: "Identity providers" })).toBeVisible()
  await expect(page.getByText("HRIS Sandbox")).toBeVisible()
})

test("creating an API key reveals and copies the one-time value", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"])
  await page.route("**/auth/session", (route) => json(route, session))
  await page.route(`**/api/identity-providers/${providerId}`, (route) => json(route, { identityProvider: { id: providerId, slug: "hris-sandbox", issuer: "hris", displayName: "HRIS Sandbox", status: "active", metadata: {}, createdAt: "2026-07-18T00:00:00Z", updatedAt: "2026-07-18T00:00:00Z" } }))
  await page.route(`**/api/identity-providers/${providerId}/api-keys?**`, (route) => json(route, { apiKeys: [], nextCursor: null }))
  await page.route(`**/api/identity-providers/${providerId}/assertion-keys?**`, (route) => json(route, { assertionKeys: [], nextCursor: null }))
  await page.route(`**/api/identity-providers/${providerId}/api-keys`, (route) => json(route, { apiKey: { id: "key-1", providerId, name: "Automation", keyPrefix: "asmith_", status: "active", expiresAt: null, revokedAt: null, lastUsedAt: null, createdAt: "2026-07-18T00:00:00Z", updatedAt: "2026-07-18T00:00:00Z", rawKey: "asmith_one_time_key" } }, 201))

  await page.goto(`/providers/${providerId}`)
  await expect(page.getByRole("heading", { name: "API keys" })).toBeVisible()
  await page.getByRole("button", { name: "Create", exact: true }).first().click()
  await page.getByLabel("Name", { exact: true }).fill("Automation")
  await page.getByRole("button", { name: "Create API key" }).click()
  await expect(page.getByTestId("one-time-secret")).toHaveText("asmith_one_time_key")
  await page.getByRole("button", { name: "Copy secret" }).click()
  await expect(page.getByRole("button", { name: "Copied" })).toBeVisible()
  expect(await page.evaluate(() => (globalThis as unknown as { navigator: { clipboard: { readText(): Promise<string> } } }).navigator.clipboard.readText())).toBe("asmith_one_time_key")
})

test("audit filters and cursor load-more update the request", async ({ page }) => {
  await page.route("**/auth/session", (route) => json(route, session))
  const requests: string[] = []
  await page.route("**/api/audit-events?**", async (route) => {
    requests.push(route.request().url())
    const url = new URL(route.request().url())
    const withCursor = url.searchParams.has("cursor")
    await json(route, { auditEvents: [{ id: withCursor ? "event-2" : "event-1", action: "identity_provider.create", outcome: "success", actor: { kind: "admin_operator", identifier: "admin", operatorId: operator.id, requestId: "request-1", ipAddress: "127.0.0.1" }, resourceType: "identity_provider", resourceId: providerId, metadata: {}, occurredAt: "2026-07-18T00:00:00Z" }], nextCursor: withCursor ? null : "next-page" })
  })

  await page.goto("/audit")
  await expect(page.getByText("identity_provider.create").first()).toBeVisible()
  await page.getByLabel("Outcome").selectOption("success")
  await page.getByRole("button", { name: "Apply filters" }).click()
  await expect.poll(() => requests.some((value) => value.includes("outcome=success"))).toBe(true)
  await page.getByRole("button", { name: "Load more" }).click()
  await expect.poll(() => requests.some((value) => value.includes("cursor=next-page"))).toBe(true)
})
