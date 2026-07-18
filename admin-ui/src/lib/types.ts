export type Operator = {
  id: string
  username: string
  displayName: string
  status: string
}

export type SessionResponse = {
  operator: Operator
  session: { idleExpiresAt: string; absoluteExpiresAt: string }
}

export type IdentityProvider = {
  id: string
  slug: string
  issuer: string
  displayName: string
  status: "active" | "disabled"
  metadata: Record<string, unknown>
  createdAt: string
  updatedAt: string
}

export type ApiKey = {
  id: string
  providerId: string
  name: string
  keyPrefix: string
  status: string
  expiresAt: string | null
  revokedAt: string | null
  lastUsedAt: string | null
  createdAt: string
  updatedAt: string
  rawKey?: string
}

export type AssertionKey = {
  id: string
  providerId: string
  kid: string
  alg: string
  status: string
  expiresAt: string | null
  revokedAt: string | null
  createdAt: string
  updatedAt: string
  rawSecret?: string
}

export type AuditEvent = {
  id: string
  action: string
  outcome: "success" | "denied" | "failed"
  actor: {
    kind: string
    identifier: string | null
    operatorId: string | null
    requestId: string | null
    ipAddress: string | null
  }
  resourceType: string
  resourceId: string | null
  metadata: Record<string, unknown>
  occurredAt: string
}

export type CursorPage<T, K extends string> = { nextCursor: string | null } & Record<K, T[]>
