import { useCallback, useEffect, useMemo, useState } from "react"
import { applyTheme, getInitialTheme, THEME_STORAGE_KEY, type Theme } from "../lib/theme"
import { ThemeContext } from "./theme-context"

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setTheme] = useState<Theme>(getInitialTheme)

  useEffect(() => {
    applyTheme(theme)
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, theme)
    } catch {
      // The active theme still works even when it cannot be persisted.
    }
  }, [theme])

  const toggleTheme = useCallback(() => {
    setTheme((current) => current === "dark" ? "light" : "dark")
  }, [])
  const value = useMemo(() => ({ theme, toggleTheme }), [theme, toggleTheme])

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
}
