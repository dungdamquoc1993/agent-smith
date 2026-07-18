import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, expect, it } from "vitest"
import { THEME_STORAGE_KEY } from "../lib/theme"
import { ThemeProvider } from "./theme-provider"
import { ThemeToggle } from "./theme-toggle"

beforeEach(() => {
  window.localStorage.clear()
  document.documentElement.classList.remove("dark")
  document.documentElement.style.colorScheme = ""
})

it("switches theme and persists the choice", async () => {
  const user = userEvent.setup()
  render(<ThemeProvider><ThemeToggle /></ThemeProvider>)

  await user.click(screen.getByRole("button", { name: "Switch to dark mode" }))

  await waitFor(() => {
    expect(document.documentElement).toHaveClass("dark")
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark")
  })
  expect(screen.getByRole("button", { name: "Switch to light mode" })).toBeInTheDocument()
})
