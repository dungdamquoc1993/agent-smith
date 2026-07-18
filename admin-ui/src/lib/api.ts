const MUTATION_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"])
const CSRF_COOKIE_NAMES = ["__Host-agent_smith_admin_csrf", "agent_smith_admin_csrf"]

type ApiErrorBody = { error?: { code?: string; message?: string } }

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
    public readonly requestId?: string,
  ) {
    super(message)
    this.name = "ApiError"
  }
}

let unauthorizedHandler: (() => void) | undefined

export function setUnauthorizedHandler(handler: (() => void) | undefined) {
  unauthorizedHandler = handler
}

export function readCsrfToken(cookie = document.cookie): string | undefined {
  const values = new Map(
    cookie.split(";").map((part) => {
      const [name, ...rest] = part.trim().split("=")
      return [name, decodeURIComponent(rest.join("="))]
    }),
  )
  for (const name of CSRF_COOKIE_NAMES) {
    const value = values.get(name)
    if (value) return value
  }
  return undefined
}

function statusMessage(status: number, fallback: string): string {
  if (fallback) return fallback
  if (status === 403) return "This action was denied. Refresh the page and try again."
  if (status === 409) return "That value is already in use. Review the form and try again."
  if (status === 422) return "Some submitted values are invalid. Review the form and try again."
  if (status === 503) return "The admin service is temporarily unavailable. Try again shortly."
  return "The request could not be completed."
}

export async function apiRequest<T>(
  path: string,
  options: RequestInit & { csrf?: boolean; redirectOnUnauthorized?: boolean } = {},
): Promise<T> {
  const method = (options.method ?? "GET").toUpperCase()
  const headers = new Headers(options.headers)
  if (options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json")
  if ((options.csrf ?? MUTATION_METHODS.has(method)) && path !== "/auth/sign-in") {
    const token = readCsrfToken()
    if (token) headers.set("X-CSRF-Token", token)
  }

  const response = await fetch(path, {
    ...options,
    headers,
    credentials: "same-origin",
  })
  const body = (await response.json().catch(() => ({}))) as T & ApiErrorBody

  if (!response.ok) {
    const requestId = response.headers.get("X-Request-ID") ?? undefined
    const error = new ApiError(
      response.status,
      body.error?.code ?? "request_failed",
      statusMessage(response.status, body.error?.message ?? ""),
      requestId,
    )
    if (response.status === 401 && options.redirectOnUnauthorized !== false) unauthorizedHandler?.()
    throw error
  }
  return body
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return `${error.message}${error.requestId ? ` (Request ID: ${error.requestId})` : ""}`
  }
  return error instanceof Error ? error.message : "Something went wrong."
}
