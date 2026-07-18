import { expect, it } from "vitest"
import { providerSchema } from "./schemas"

it("validates provider slugs and JSON-object metadata", () => {
  const base = { slug: "hris-sandbox", issuer: "hris", displayName: "HRIS", status: "active" as const, metadata: "{}" }
  expect(providerSchema.safeParse(base).success).toBe(true)
  expect(providerSchema.safeParse({ ...base, slug: "Not Allowed" }).success).toBe(false)
  expect(providerSchema.safeParse({ ...base, metadata: "[]" }).success).toBe(false)
  expect(providerSchema.safeParse({ ...base, metadata: "{" }).success).toBe(false)
})
