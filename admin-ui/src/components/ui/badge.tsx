import type { HTMLAttributes } from "react"
import { cn } from "../../lib/utils"

export function Badge({ className, ...props }: HTMLAttributes<HTMLSpanElement>) {
  const value = String(props.children ?? "").toLowerCase()
  const color = value === "active" || value === "success"
    ? "bg-emerald-50 text-emerald-700 ring-emerald-600/20 dark:bg-emerald-500/15 dark:text-emerald-300 dark:ring-emerald-400/25"
    : value === "denied" || value === "failed" || value === "revoked" || value === "disabled"
      ? "bg-red-50 text-red-700 ring-red-600/20 dark:bg-red-500/15 dark:text-red-300 dark:ring-red-400/25"
      : "bg-slate-100 text-slate-700 ring-slate-600/20 dark:bg-slate-700 dark:text-slate-200 dark:ring-slate-500/30"
  return <span className={cn("inline-flex items-center rounded-full px-2 py-1 text-xs font-medium capitalize ring-1 ring-inset", color, className)} {...props} />
}
