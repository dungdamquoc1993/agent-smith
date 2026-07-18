import * as React from "react"
import { cn } from "../../lib/utils"

export const Textarea = React.forwardRef<HTMLTextAreaElement, React.TextareaHTMLAttributes<HTMLTextAreaElement>>(
  ({ className, ...props }, ref) => (
    <textarea ref={ref} className={cn("flex min-h-28 w-full rounded-md border bg-card px-3 py-2 font-mono text-sm shadow-sm placeholder:text-muted-foreground disabled:cursor-not-allowed disabled:opacity-50", className)} {...props} />
  ),
)
Textarea.displayName = "Textarea"
