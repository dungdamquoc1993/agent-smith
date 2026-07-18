import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { BrowserRouter } from "react-router-dom"
import { App } from "./app"
import { ThemeProvider } from "./components/theme-provider"
import { applyTheme, getInitialTheme } from "./lib/theme"
import "./index.css"

applyTheme(getInitialTheme())

export const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false }, mutations: { retry: false } },
})

createRoot(document.getElementById("root")!).render(
  <StrictMode><ThemeProvider><QueryClientProvider client={queryClient}><BrowserRouter><App /></BrowserRouter></QueryClientProvider></ThemeProvider></StrictMode>,
)
