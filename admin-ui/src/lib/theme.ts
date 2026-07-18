export type Theme = "light" | "dark"

export const THEME_STORAGE_KEY = "agent-smith-admin-theme"

export function getInitialTheme(): Theme {
  try {
    const savedTheme = window.localStorage.getItem(THEME_STORAGE_KEY)
    if (savedTheme === "light" || savedTheme === "dark") return savedTheme
  } catch {
    // Storage can be unavailable in privacy-restricted browser contexts.
  }

  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light"
}

export function applyTheme(theme: Theme) {
  document.documentElement.classList.toggle("dark", theme === "dark")
  document.documentElement.style.colorScheme = theme
}
