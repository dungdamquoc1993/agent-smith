import { ArrowLeft, FileQuestion } from "lucide-react"
import { Link } from "react-router-dom"
import { ThemeToggle } from "../components/theme-toggle"

export function NotFoundPage() {
  return <main className="relative grid min-h-screen place-items-center bg-background p-6 text-center"><ThemeToggle className="absolute right-4 top-4 sm:right-6 sm:top-6" /><div><FileQuestion className="mx-auto h-12 w-12 text-slate-400 dark:text-slate-500" /><p className="mt-5 text-sm font-medium text-blue-700 dark:text-blue-300">404</p><h1 className="mt-2 text-3xl font-semibold">Page not found</h1><p className="mt-3 text-muted-foreground">The admin page you requested does not exist.</p><Link className="mt-6 inline-flex items-center gap-2 text-sm font-medium text-blue-700 hover:underline dark:text-blue-300" to="/providers"><ArrowLeft className="h-4 w-4" />Back to identity providers</Link></div></main>
}
