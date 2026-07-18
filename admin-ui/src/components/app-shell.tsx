import { useState } from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { KeyRound, LogOut, Menu, ScrollText, ShieldCheck, X } from "lucide-react"
import { NavLink, useNavigate } from "react-router-dom"
import type { Operator } from "../lib/types"
import { apiRequest } from "../lib/api"
import { cn } from "../lib/utils"
import { Button } from "./ui/button"
import { ThemeToggle } from "./theme-toggle"

const links = [
  { to: "/providers", label: "Identity providers", icon: KeyRound },
  { to: "/audit", label: "Audit log", icon: ScrollText },
]

export function AppShell({ operator, children }: { operator: Operator; children: React.ReactNode }) {
  const [mobileOpen, setMobileOpen] = useState(false)
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const signOut = useMutation({
    mutationFn: () => apiRequest<{ signedOut: boolean }>("/auth/sign-out", { method: "POST" }),
    onSuccess: () => { queryClient.clear(); navigate("/sign-in", { replace: true }) },
  })
  return <div className="min-h-screen lg:grid lg:grid-cols-[260px_1fr]">
    <header className="sticky top-0 z-30 flex h-16 items-center justify-between border-b bg-card/95 px-4 backdrop-blur lg:hidden">
      <Brand compact /><div className="flex items-center gap-1"><ThemeToggle /><Button variant="ghost" size="icon" onClick={() => setMobileOpen((value) => !value)} aria-label="Toggle navigation">{mobileOpen ? <X /> : <Menu />}</Button></div>
    </header>
    <aside className={cn("fixed inset-x-0 top-16 z-20 border-b bg-card p-4 shadow-lg lg:sticky lg:top-0 lg:block lg:h-screen lg:border-b-0 lg:border-r lg:p-5 lg:shadow-none", mobileOpen ? "block" : "hidden")}>
      <div className="hidden items-start justify-between lg:flex"><Brand /><ThemeToggle /></div>
      <nav className="mt-2 space-y-1 lg:mt-10" aria-label="Admin navigation">
        {links.map(({ to, label, icon: Icon }) => <NavLink key={to} to={to} onClick={() => setMobileOpen(false)} className={({ isActive }) => cn("flex items-center gap-3 rounded-md px-3 py-2.5 text-sm font-medium transition-colors", isActive ? "bg-blue-50 text-blue-700 dark:bg-blue-500/15 dark:text-blue-300" : "text-slate-600 hover:bg-slate-100 hover:text-slate-950 dark:text-slate-400 dark:hover:bg-slate-800 dark:hover:text-slate-50")}><Icon className="h-4 w-4" />{label}</NavLink>)}
      </nav>
      <div className="mt-5 border-t pt-4 lg:absolute lg:inset-x-5 lg:bottom-5">
        <p className="truncate text-sm font-medium">{operator.displayName}</p><p className="truncate text-xs text-muted-foreground">{operator.username}</p>
        <Button className="mt-3 w-full justify-start" variant="ghost" size="sm" onClick={() => signOut.mutate()} disabled={signOut.isPending}><LogOut className="h-4 w-4" />{signOut.isPending ? "Signing out…" : "Sign out"}</Button>
      </div>
    </aside>
    <main className="min-w-0"><div className="mx-auto max-w-7xl p-5 sm:p-8 lg:p-10">{children}</div></main>
  </div>
}

function Brand({ compact = false }: { compact?: boolean }) {
  return <div className="flex items-center gap-3"><span className="flex h-9 w-9 items-center justify-center rounded-lg bg-slate-950 text-white dark:bg-blue-500"><ShieldCheck className="h-5 w-5" /></span><div><p className="text-sm font-semibold leading-none">Agent Smith</p>{!compact && <p className="mt-1 text-xs text-muted-foreground">Control plane</p>}</div></div>
}
