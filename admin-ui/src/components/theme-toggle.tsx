import { Moon, Sun } from "lucide-react"
import { useTheme } from "./theme-context"
import { Button } from "./ui/button"

export function ThemeToggle({ className }: { className?: string }) {
  const { theme, toggleTheme } = useTheme()
  const nextTheme = theme === "dark" ? "light" : "dark"

  return (
    <Button
      className={className}
      variant="ghost"
      size="icon"
      onClick={toggleTheme}
      aria-label={`Switch to ${nextTheme} mode`}
      title={`Switch to ${nextTheme} mode`}
    >
      {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
    </Button>
  )
}
