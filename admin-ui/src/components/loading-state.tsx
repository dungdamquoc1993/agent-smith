import { LoaderCircle } from "lucide-react"

export function LoadingState({ label = "Loading" }: { label?: string }) {
  return <div className="flex min-h-40 items-center justify-center gap-2 text-sm text-muted-foreground" role="status"><LoaderCircle className="h-4 w-4 animate-spin" />{label}</div>
}
