import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { expect, it, vi } from "vitest"
import { SecretRevealDialog } from "./secret-reveal-dialog"

it("blocks closing a one-time secret until the operator confirms it is saved", async () => {
  const user = userEvent.setup()
  const writeText = vi.fn().mockResolvedValue(undefined)
  Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } })
  const onClose = vi.fn()
  render(<SecretRevealDialog open label="API key" secret="asmith_secret_once" onClose={onClose} />)

  const close = screen.getByRole("button", { name: "Close securely" })
  expect(close).toBeDisabled()
  expect(screen.getByTestId("one-time-secret")).toHaveTextContent("asmith_secret_once")
  await user.click(screen.getByRole("button", { name: "Copy secret" }))
  expect(writeText).toHaveBeenCalledWith("asmith_secret_once")
  await user.click(screen.getByRole("checkbox", { name: /I saved this credential/i }))
  await user.click(close)
  expect(onClose).toHaveBeenCalledOnce()
})
