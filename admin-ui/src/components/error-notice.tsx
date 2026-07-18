import { AlertCircle } from "lucide-react"
import { errorMessage } from "../lib/api"

export function ErrorNotice({ error, title = "Unable to complete the request" }: { error: unknown; title?: string }) {
  return (
    <div role="alert" className="flex gap-3 rounded-md border border-red-200 bg-red-50 p-4 text-red-900">
      <AlertCircle className="mt-0.5 h-5 w-5 shrink-0" aria-hidden="true" />
      <div><p className="text-sm font-medium">{title}</p><p className="mt-1 text-sm text-red-800">{errorMessage(error)}</p></div>
    </div>
  )
}
