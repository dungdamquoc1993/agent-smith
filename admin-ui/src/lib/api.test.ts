import { afterEach, describe, expect, it, vi } from "vitest"
import { apiRequest, readCsrfToken, setUnauthorizedHandler } from "./api"

afterEach(() => {
  vi.unstubAllGlobals()
  setUnauthorizedHandler(undefined)
  document.cookie = "agent_smith_admin_csrf=; Max-Age=0; Path=/"
  document.cookie = "__Host-agent_smith_admin_csrf=; Max-Age=0; Path=/"
})

describe("admin API client", () => {
  it("reads both CSRF cookie profiles and sends the token on mutations", async () => {
    document.cookie = "agent_smith_admin_csrf=dev-token; Path=/"
    expect(readCsrfToken()).toBe("dev-token")
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 200, headers: { "Content-Type": "application/json" } }))
    vi.stubGlobal("fetch", fetchMock)

    await apiRequest("/api/example", { method: "POST", body: JSON.stringify({ value: 1 }) })

    const [, init] = fetchMock.mock.calls[0]
    expect(init.credentials).toBe("same-origin")
    expect((init.headers as Headers).get("X-CSRF-Token")).toBe("dev-token")
  })

  it("never sends CSRF on sign-in and handles unauthorized responses", async () => {
    document.cookie = "agent_smith_admin_csrf=dev-token; Path=/"
    const unauthorized = vi.fn()
    setUnauthorizedHandler(unauthorized)
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: { code: "invalid_credentials", message: "Invalid username or password." } }), { status: 401, headers: { "Content-Type": "application/json", "X-Request-ID": "request-1" } }))
    vi.stubGlobal("fetch", fetchMock)

    await expect(apiRequest("/auth/sign-in", { method: "POST", body: "{}" })).rejects.toMatchObject({ status: 401, requestId: "request-1" })
    expect((fetchMock.mock.calls[0][1].headers as Headers).has("X-CSRF-Token")).toBe(false)
    expect(unauthorized).toHaveBeenCalledOnce()
  })
})
