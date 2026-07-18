import { useState } from "react"
import { zodResolver } from "@hookform/resolvers/zod"
import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { ArrowRight, Plus, ServerCog } from "lucide-react"
import { useForm } from "react-hook-form"
import { Link } from "react-router-dom"
import { apiRequest } from "../lib/api"
import { providerSchema, type ProviderFormValues } from "../lib/schemas"
import type { CursorPage, IdentityProvider } from "../lib/types"
import { formatDate } from "../lib/utils"
import { ErrorNotice } from "../components/error-notice"
import { LoadingState } from "../components/loading-state"
import { Badge } from "../components/ui/badge"
import { Button } from "../components/ui/button"
import { Card, CardContent } from "../components/ui/card"
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "../components/ui/dialog"
import { Input } from "../components/ui/input"
import { Textarea } from "../components/ui/textarea"

type ProviderPage = CursorPage<IdentityProvider, "identityProviders">

export function ProvidersPage() {
  const [createOpen, setCreateOpen] = useState(false)
  const providers = useInfiniteQuery({
    queryKey: ["providers"],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) => apiRequest<ProviderPage>(`/api/identity-providers?limit=25${pageParam ? `&cursor=${encodeURIComponent(pageParam)}` : ""}`),
    getNextPageParam: (page) => page.nextCursor ?? undefined,
  })
  const rows = providers.data?.pages.flatMap((page) => page.identityProviders) ?? []
  return <>
    <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-end"><div><h1 className="page-title">Identity providers</h1><p className="page-subtitle">Configure trusted issuers and their API and assertion credentials.</p></div><Button onClick={() => setCreateOpen(true)}><Plus className="h-4 w-4" />Create provider</Button></div>
    <div className="mt-8">
      {providers.isPending ? <LoadingState label="Loading identity providers…" /> : providers.isError ? <ErrorNotice error={providers.error} /> : rows.length === 0 ? <Card><CardContent className="flex flex-col items-center py-14 text-center"><span className="rounded-full bg-blue-50 p-4 text-blue-700 dark:bg-blue-500/15 dark:text-blue-300"><ServerCog className="h-7 w-7" /></span><h2 className="mt-4 text-lg font-semibold">No identity providers</h2><p className="mt-2 max-w-md text-sm text-muted-foreground">Create your first trusted provider to issue API keys or signed assertions.</p><Button className="mt-5" onClick={() => setCreateOpen(true)}><Plus className="h-4 w-4" />Create provider</Button></CardContent></Card> : <>
        <div className="overflow-hidden rounded-lg border bg-card shadow-card"><div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead className="border-b bg-slate-50 text-xs uppercase tracking-wide text-slate-500 dark:bg-slate-900 dark:text-slate-400"><tr><th className="px-5 py-3 font-medium">Provider</th><th className="px-5 py-3 font-medium">Issuer</th><th className="px-5 py-3 font-medium">Status</th><th className="px-5 py-3 font-medium">Updated</th><th className="px-5 py-3"><span className="sr-only">Open</span></th></tr></thead><tbody className="divide-y">{rows.map((provider) => <tr key={provider.id} className="hover:bg-slate-50 dark:hover:bg-slate-900/70"><td className="px-5 py-4"><p className="font-medium text-slate-950 dark:text-slate-50">{provider.displayName}</p><p className="mt-0.5 font-mono text-xs text-muted-foreground">{provider.slug}</p></td><td className="max-w-xs truncate px-5 py-4 text-slate-600 dark:text-slate-300">{provider.issuer}</td><td className="px-5 py-4"><Badge>{provider.status}</Badge></td><td className="whitespace-nowrap px-5 py-4 text-slate-600 dark:text-slate-300">{formatDate(provider.updatedAt)}</td><td className="px-5 py-4 text-right"><Link className="inline-flex items-center gap-1 font-medium text-blue-700 hover:underline dark:text-blue-300" to={`/providers/${provider.id}`}>Manage<ArrowRight className="h-4 w-4" /></Link></td></tr>)}</tbody></table></div></div>
        {providers.hasNextPage && <div className="mt-5 text-center"><Button variant="outline" onClick={() => providers.fetchNextPage()} disabled={providers.isFetchingNextPage}>{providers.isFetchingNextPage ? "Loading…" : "Load more"}</Button></div>}
      </>}
    </div>
    <CreateProviderDialog open={createOpen} onOpenChange={setCreateOpen} />
  </>
}

function CreateProviderDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (value: boolean) => void }) {
  const client = useQueryClient()
  const form = useForm<ProviderFormValues>({ resolver: zodResolver(providerSchema), defaultValues: { slug: "", issuer: "", displayName: "", status: "active", metadata: "{}" } })
  const mutation = useMutation({
    mutationFn: (values: ProviderFormValues) => apiRequest<{ identityProvider: IdentityProvider }>("/api/identity-providers", { method: "POST", body: JSON.stringify({ ...values, metadata: JSON.parse(values.metadata) }) }),
    onSuccess: () => { client.invalidateQueries({ queryKey: ["providers"] }); form.reset(); onOpenChange(false) },
  })
  function changeOpen(value: boolean) { if (!mutation.isPending) { onOpenChange(value); if (!value) { form.reset(); mutation.reset() } } }
  return <Dialog open={open} onOpenChange={changeOpen}><DialogContent className="max-h-[90vh] overflow-y-auto"><DialogTitle>Create identity provider</DialogTitle><DialogDescription>Add a trusted issuer. You can create credentials after the provider is saved.</DialogDescription>
    <form className="mt-5 space-y-4" onSubmit={form.handleSubmit((values) => mutation.mutate(values))} noValidate>
      {mutation.isError && <ErrorNotice error={mutation.error} />}
      <Field label="Display name" id="create-display-name" error={form.formState.errors.displayName?.message}><Input id="create-display-name" {...form.register("displayName")} /></Field>
      <Field label="Slug" id="create-slug" error={form.formState.errors.slug?.message} help="Lowercase letters, numbers, underscores, and dashes."><Input id="create-slug" autoCapitalize="none" {...form.register("slug")} /></Field>
      <Field label="Issuer" id="create-issuer" error={form.formState.errors.issuer?.message}><Input id="create-issuer" autoCapitalize="none" {...form.register("issuer")} /></Field>
      <Field label="Status" id="create-status"><select id="create-status" className="h-10 w-full rounded-md border bg-card px-3 text-sm" {...form.register("status")}><option value="active">Active</option><option value="disabled">Disabled</option></select></Field>
      <Field label="Metadata (JSON)" id="create-metadata" error={form.formState.errors.metadata?.message}><Textarea id="create-metadata" spellCheck={false} {...form.register("metadata")} /></Field>
      <div className="flex justify-end gap-3 pt-2"><Button type="button" variant="outline" onClick={() => changeOpen(false)}>Cancel</Button><Button type="submit" disabled={mutation.isPending}>{mutation.isPending ? "Creating…" : "Create provider"}</Button></div>
    </form>
  </DialogContent></Dialog>
}

export function Field({ label, id, error, help, children }: { label: string; id: string; error?: string; help?: string; children: React.ReactNode }) {
  return <div><label className="field-label" htmlFor={id}>{label}</label>{children}{help && <p className="field-help">{help}</p>}{error && <p className="field-error">{error}</p>}</div>
}
