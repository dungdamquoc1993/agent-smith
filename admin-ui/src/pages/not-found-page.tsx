import { ArrowLeft, FileQuestion } from "lucide-react"
import { Link } from "react-router-dom"

export function NotFoundPage() {
  return <main className="grid min-h-screen place-items-center bg-background p-6 text-center"><div><FileQuestion className="mx-auto h-12 w-12 text-slate-400" /><p className="mt-5 text-sm font-medium text-blue-700">404</p><h1 className="mt-2 text-3xl font-semibold">Page not found</h1><p className="mt-3 text-muted-foreground">The admin page you requested does not exist.</p><Link className="mt-6 inline-flex items-center gap-2 text-sm font-medium text-blue-700 hover:underline" to="/providers"><ArrowLeft className="h-4 w-4" />Back to identity providers</Link></div></main>
}
