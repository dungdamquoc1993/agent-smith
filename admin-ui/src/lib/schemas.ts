import { z } from "zod"

const metadataString = z.string().superRefine((value, context) => {
  try {
    const parsed: unknown = JSON.parse(value)
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      context.addIssue({ code: "custom", message: "Metadata must be a JSON object." })
    }
  } catch {
    context.addIssue({ code: "custom", message: "Metadata must be valid JSON." })
  }
})

export const providerSchema = z.object({
  slug: z.string().trim().min(2).max(128).regex(/^[a-z0-9](?:[a-z0-9_-]{0,126}[a-z0-9])$/, "Use lowercase letters, numbers, underscores, or dashes."),
  issuer: z.string().trim().min(2).max(128),
  displayName: z.string().trim().min(1).max(255),
  status: z.enum(["active", "disabled"]),
  metadata: metadataString,
})

export type ProviderFormValues = z.infer<typeof providerSchema>

export const signInSchema = z.object({
  username: z.string().trim().min(1, "Username is required.").max(128),
  password: z.string().min(1, "Password is required.").max(4096),
})

export const apiKeySchema = z.object({
  name: z.string().trim().min(1, "Name is required.").max(255),
  expiresAt: z.string(),
})

export const assertionKeySchema = z.object({
  kid: z.string().trim().min(1, "Key ID is required.").max(128),
  expiresAt: z.string(),
})
