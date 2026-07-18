import { useEffect } from "react"
import { zodResolver } from "@hookform/resolvers/zod"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { ArrowLeft, Save } from "lucide-react"
import { useForm } from "react-hook-form"
import { Link, useParams } from "react-router-dom"
import { ApiError, apiRequest } from "../lib/api"
import { providerSchema, type ProviderFormValues } from "../lib/schemas"
import type { IdentityProvider } from "../lib/types"
import { formatDate } from "../lib/utils"
import { ApiKeysSection, AssertionKeysSection } from "../components/credential-sections"
import { ErrorNotice } from "../components/error-notice"
import { LoadingState } from "../components/loading-state"
import { Field } from "./providers-page"
import { Badge } from "../components/ui/badge"
import { Button } from "../components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../components/ui/card"
import { Input } from "../components/ui/input"
import { Textarea } from "../components/ui/textarea"

export function ProviderDetailPage() {
  const { providerId = "" } = useParams()
  const client = useQueryClient()
  const query = useQuery({ queryKey: ["provider", providerId], queryFn: () => apiRequest<{ identityProvider: IdentityProvider }>(`/api/identity-providers/${providerId}`), enabled: Boolean(providerId) })
  const form = useForm<ProviderFormValues>({ resolver: zodResolver(providerSchema), defaultValues: { slug: "", issuer: "", displayName: "", status: "active", metadata: "{}" } })
  useEffect(() => {
    if (query.data) {
      const provider = query.data.identityProvider
      form.reset({ slug: provider.slug, issuer: provider.issuer, displayName: provider.displayName, status: provider.status, metadata: JSON.stringify(provider.metadata, null, 2) })
    }
  }, [query.data, form])
  const update = useMutation({
    mutationFn: (values: ProviderFormValues) => apiRequest<{ identityProvider: IdentityProvider }>(`/api/identity-providers/${providerId}`, { method: "PATCH", body: JSON.stringify({ ...values, metadata: JSON.parse(values.metadata) }) }),
    onSuccess: (data) => { client.setQueryData(["provider", providerId], data); client.invalidateQueries({ queryKey: ["providers"] }); form.reset({ ...data.identityProvider, metadata: JSON.stringify(data.identityProvider.metadata, null, 2) }) },
  })
  if (query.isPending) return <LoadingState label="Loading provider…" />
  if (query.isError) {
    if (query.error instanceof ApiError && query.error.status === 404) return <div><Link className="inline-flex items-center gap-2 text-sm font-medium text-blue-700 dark:text-blue-300" to="/providers"><ArrowLeft className="h-4 w-4" />Identity providers</Link><div className="mt-8"><ErrorNotice error={query.error} title="Provider not found" /></div></div>
    return <ErrorNotice error={query.error} />
  }
  const provider = query.data.identityProvider
  return <>
    <Link className="inline-flex items-center gap-2 text-sm font-medium text-slate-600 hover:text-slate-950 dark:text-slate-400 dark:hover:text-slate-50" to="/providers"><ArrowLeft className="h-4 w-4" />Identity providers</Link>
    <div className="mt-5 flex flex-col justify-between gap-3 sm:flex-row sm:items-end"><div><div className="flex items-center gap-3"><h1 className="page-title">{provider.displayName}</h1><Badge>{provider.status}</Badge></div><p className="page-subtitle font-mono">{provider.slug}</p></div><p className="text-xs text-muted-foreground">Updated {formatDate(provider.updatedAt)}</p></div>
    <div className="mt-8 space-y-6">
      <Card><CardHeader><CardTitle>Provider overview</CardTitle><CardDescription>Changes take effect for new authentication attempts immediately.</CardDescription></CardHeader><CardContent><form className="grid gap-5 sm:grid-cols-2" onSubmit={form.handleSubmit((values) => update.mutate(values))} noValidate>
        {update.isError && <div className="sm:col-span-2"><ErrorNotice error={update.error} /></div>}
        {update.isSuccess && !form.formState.isDirty && <div role="status" className="rounded-md border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-800 dark:border-emerald-900/60 dark:bg-emerald-950/40 dark:text-emerald-300 sm:col-span-2">Provider saved.</div>}
        <Field label="Display name" id="detail-display-name" error={form.formState.errors.displayName?.message}><Input id="detail-display-name" {...form.register("displayName")} /></Field>
        <Field label="Slug" id="detail-slug" error={form.formState.errors.slug?.message}><Input id="detail-slug" {...form.register("slug")} /></Field>
        <Field label="Issuer" id="detail-issuer" error={form.formState.errors.issuer?.message}><Input id="detail-issuer" {...form.register("issuer")} /></Field>
        <Field label="Status" id="detail-status"><select id="detail-status" className="h-10 w-full rounded-md border bg-card px-3 text-sm" {...form.register("status")}><option value="active">Active</option><option value="disabled">Disabled</option></select></Field>
        <div className="sm:col-span-2"><Field label="Metadata (JSON)" id="detail-metadata" error={form.formState.errors.metadata?.message}><Textarea id="detail-metadata" className="min-h-40" spellCheck={false} {...form.register("metadata")} /></Field></div>
        <div className="flex justify-end sm:col-span-2"><Button type="submit" disabled={update.isPending || !form.formState.isDirty}><Save className="h-4 w-4" />{update.isPending ? "Saving…" : "Save changes"}</Button></div>
      </form></CardContent></Card>
      <div className="grid gap-6 xl:grid-cols-2"><ApiKeysSection providerId={providerId} /><AssertionKeysSection providerId={providerId} /></div>
    </div>
  </>
}
