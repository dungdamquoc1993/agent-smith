import { Button } from "./ui/button"
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "./ui/dialog"

export function ConfirmDialog({ open, onOpenChange, title, description, confirmLabel = "Confirm", pending, onConfirm }: {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description: string
  confirmLabel?: string
  pending?: boolean
  onConfirm: () => void
}) {
  return <Dialog open={open} onOpenChange={onOpenChange}><DialogContent>
    <DialogTitle>{title}</DialogTitle><DialogDescription>{description}</DialogDescription>
    <div className="mt-6 flex justify-end gap-3"><Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button><Button variant="destructive" onClick={onConfirm} disabled={pending}>{pending ? "Working…" : confirmLabel}</Button></div>
  </DialogContent></Dialog>
}
