import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { afterEach, expect, it, vi } from "vitest"
import { App } from "./app"
import { ThemeProvider } from "./components/theme-provider"

afterEach(() => vi.unstubAllGlobals())

it("bootstraps the session and redirects unauthenticated routes to sign in", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: { code: "admin_session_required", message: "Authentication required." } }), { status: 401, headers: { "Content-Type": "application/json" } })))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<ThemeProvider><QueryClientProvider client={client}><MemoryRouter initialEntries={["/providers"]}><App /></MemoryRouter></QueryClientProvider></ThemeProvider>)

  expect(await screen.findByRole("heading", { name: "Sign in to Agent Smith" })).toBeInTheDocument()
  expect(fetch).toHaveBeenCalledWith("/auth/session", expect.objectContaining({ credentials: "same-origin" }))
})
