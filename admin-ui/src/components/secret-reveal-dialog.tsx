import { useState } from "react"
import { Check, Copy, KeyRound, TriangleAlert } from "lucide-react"
import { Button } from "./ui/button"
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "./ui/dialog"

export function SecretRevealDialog({ open, label, secret, onClose }: { open: boolean; label: string; secret: string; onClose: () => void }) {
  if (!open) return null
  return <OpenSecretRevealDialog label={label} secret={secret} onClose={onClose} />
}

function OpenSecretRevealDialog({ label, secret, onClose }: { label: string; secret: string; onClose: () => void }) {
  const [saved, setSaved] = useState(false)
  const [copied, setCopied] = useState(false)
  async function copy() {
    await navigator.clipboard.writeText(secret)
    setCopied(true)
  }
  return <Dialog open onOpenChange={(next) => { if (!next && saved) onClose() }}>
    <DialogContent hideClose onEscapeKeyDown={(event) => { if (!saved) event.preventDefault() }} onPointerDownOutside={(event) => event.preventDefault()}>
      <div className="mb-4 flex h-11 w-11 items-center justify-center rounded-full bg-amber-100 text-amber-700"><KeyRound className="h-5 w-5" /></div>
      <DialogTitle>Save this {label}</DialogTitle>
      <DialogDescription>This secret is shown only once. Store it in your approved secret manager before closing this dialog.</DialogDescription>
      <div className="mt-5 rounded-md border bg-slate-950 p-3 text-slate-50"><code className="block break-all text-sm" data-testid="one-time-secret">{secret}</code></div>
      <Button className="mt-3 w-full" variant="outline" onClick={copy}>{copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}{copied ? "Copied" : "Copy secret"}</Button>
      <label className="mt-5 flex cursor-pointer items-start gap-3 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-950">
        <input className="mt-0.5 h-4 w-4" type="checkbox" checked={saved} onChange={(event) => setSaved(event.target.checked)} />
        <span><span className="flex items-center gap-1.5 font-medium"><TriangleAlert className="h-4 w-4" />I saved this credential</span><span className="mt-1 block text-xs leading-5 text-amber-800">I understand it cannot be viewed again.</span></span>
      </label>
      <Button className="mt-4 w-full" disabled={!saved} onClick={onClose}>Close securely</Button>
    </DialogContent>
  </Dialog>
}
