import { useState } from "react"
import { zodResolver } from "@hookform/resolvers/zod"
import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { Key, Plus, ShieldEllipsis } from "lucide-react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import { apiRequest } from "../lib/api"
import { apiKeySchema, assertionKeySchema } from "../lib/schemas"
import type { ApiKey, AssertionKey, CursorPage } from "../lib/types"
import { formatDate, toIso } from "../lib/utils"
import { ConfirmDialog } from "./confirm-dialog"
import { ErrorNotice } from "./error-notice"
import { SecretRevealDialog } from "./secret-reveal-dialog"
import { Badge } from "./ui/badge"
import { Button } from "./ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "./ui/card"
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "./ui/dialog"
import { Input } from "./ui/input"

type ApiKeyValues = z.infer<typeof apiKeySchema>
type AssertionKeyValues = z.infer<typeof assertionKeySchema>

export function ApiKeysSection({ providerId }: { providerId: string }) {
  const queryKey = ["provider-api-keys", providerId]
  const client = useQueryClient()
  const [createOpen, setCreateOpen] = useState(false)
  const [revoke, setRevoke] = useState<ApiKey | null>(null)
  const [secret, setSecret] = useState<string | null>(null)
  const form = useForm<ApiKeyValues>({ resolver: zodResolver(apiKeySchema), defaultValues: { name: "", expiresAt: "" } })
  const query = useInfiniteQuery({
    queryKey,
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) => apiRequest<CursorPage<ApiKey, "apiKeys">>(`/api/identity-providers/${providerId}/api-keys?limit=25${pageParam ? `&cursor=${encodeURIComponent(pageParam)}` : ""}`),
    getNextPageParam: (page) => page.nextCursor ?? undefined,
  })
  const create = useMutation({
    mutationFn: (values: ApiKeyValues) => apiRequest<{ apiKey: ApiKey }>(`/api/identity-providers/${providerId}/api-keys`, { method: "POST", body: JSON.stringify({ name: values.name, expiresAt: toIso(values.expiresAt) }) }),
    onSuccess: (data) => { setSecret(data.apiKey.rawKey ?? ""); setCreateOpen(false); form.reset(); client.invalidateQueries({ queryKey }) },
  })
  const revokeMutation = useMutation({
    mutationFn: (id: string) => apiRequest<{ apiKey: ApiKey }>(`/api/identity-provider-api-keys/${id}/revoke`, { method: "POST" }),
    onSuccess: () => { setRevoke(null); client.invalidateQueries({ queryKey }) },
  })
  const rows = query.data?.pages.flatMap((page) => page.apiKeys) ?? []
  return <Card><CardHeader className="flex-row items-start justify-between space-y-0"><div><CardTitle className="flex items-center gap-2"><Key className="h-5 w-5 text-blue-700" />API keys</CardTitle><CardDescription className="mt-2">Authenticate direct API access for this provider.</CardDescription></div><Button size="sm" onClick={() => setCreateOpen(true)}><Plus className="h-4 w-4" />Create</Button></CardHeader><CardContent>
    <CredentialList rows={rows} pending={query.isPending} error={query.error} hasMore={query.hasNextPage} loadingMore={query.isFetchingNextPage} loadMore={() => query.fetchNextPage()} render={(item) => <CredentialRow key={item.id} title={item.name} subtitle={`Prefix ${item.keyPrefix}`} status={item.status} expiresAt={item.expiresAt} createdAt={item.createdAt} revokedAt={item.revokedAt} onRevoke={() => setRevoke(item)} />} />
    <Dialog open={createOpen} onOpenChange={(value) => { setCreateOpen(value); if (!value) create.reset() }}><DialogContent><DialogTitle>Create API key</DialogTitle><DialogDescription>The raw key will be displayed once after creation.</DialogDescription><form className="mt-5 space-y-4" onSubmit={form.handleSubmit((values) => create.mutate(values))}>{create.isError && <ErrorNotice error={create.error} />}<div><label className="field-label" htmlFor="api-key-name">Name</label><Input id="api-key-name" {...form.register("name")} />{form.formState.errors.name && <p className="field-error">{form.formState.errors.name.message}</p>}</div><ExpiryField id="api-key-expiry" register={form.register("expiresAt")} /><div className="flex justify-end gap-3"><Button type="button" variant="outline" onClick={() => setCreateOpen(false)}>Cancel</Button><Button type="submit" disabled={create.isPending}>{create.isPending ? "Creating…" : "Create API key"}</Button></div></form></DialogContent></Dialog>
    <ConfirmDialog open={Boolean(revoke)} onOpenChange={(value) => !value && setRevoke(null)} title="Revoke API key?" description={`Applications using ${revoke?.name ?? "this key"} will immediately lose access. This cannot be undone.`} confirmLabel="Revoke key" pending={revokeMutation.isPending} onConfirm={() => revoke && revokeMutation.mutate(revoke.id)} />
    <SecretRevealDialog open={secret !== null} label="API key" secret={secret ?? ""} onClose={() => { setSecret(null); create.reset() }} />
  </CardContent></Card>
}

export function AssertionKeysSection({ providerId }: { providerId: string }) {
  const queryKey = ["provider-assertion-keys", providerId]
  const client = useQueryClient()
  const [createOpen, setCreateOpen] = useState(false)
  const [revoke, setRevoke] = useState<AssertionKey | null>(null)
  const [secret, setSecret] = useState<string | null>(null)
  const form = useForm<AssertionKeyValues>({ resolver: zodResolver(assertionKeySchema), defaultValues: { kid: "", expiresAt: "" } })
  const query = useInfiniteQuery({
    queryKey,
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) => apiRequest<CursorPage<AssertionKey, "assertionKeys">>(`/api/identity-providers/${providerId}/assertion-keys?limit=25${pageParam ? `&cursor=${encodeURIComponent(pageParam)}` : ""}`),
    getNextPageParam: (page) => page.nextCursor ?? undefined,
  })
  const create = useMutation({
    mutationFn: (values: AssertionKeyValues) => apiRequest<{ assertionKey: AssertionKey }>(`/api/identity-providers/${providerId}/assertion-keys`, { method: "POST", body: JSON.stringify({ kid: values.kid, expiresAt: toIso(values.expiresAt) }) }),
    onSuccess: (data) => { setSecret(data.assertionKey.rawSecret ?? ""); setCreateOpen(false); form.reset(); client.invalidateQueries({ queryKey }) },
  })
  const revokeMutation = useMutation({
    mutationFn: (id: string) => apiRequest<{ assertionKey: AssertionKey }>(`/api/identity-provider-assertion-keys/${id}/revoke`, { method: "POST" }),
    onSuccess: () => { setRevoke(null); client.invalidateQueries({ queryKey }) },
  })
  const rows = query.data?.pages.flatMap((page) => page.assertionKeys) ?? []
  return <Card><CardHeader className="flex-row items-start justify-between space-y-0"><div><CardTitle className="flex items-center gap-2"><ShieldEllipsis className="h-5 w-5 text-violet-700" />Assertion keys</CardTitle><CardDescription className="mt-2">Sign trusted parent-application identity assertions.</CardDescription></div><Button size="sm" onClick={() => setCreateOpen(true)}><Plus className="h-4 w-4" />Create</Button></CardHeader><CardContent>
    <CredentialList rows={rows} pending={query.isPending} error={query.error} hasMore={query.hasNextPage} loadingMore={query.isFetchingNextPage} loadMore={() => query.fetchNextPage()} render={(item) => <CredentialRow key={item.id} title={item.kid} subtitle={item.alg} status={item.status} expiresAt={item.expiresAt} createdAt={item.createdAt} revokedAt={item.revokedAt} onRevoke={() => setRevoke(item)} />} />
    <Dialog open={createOpen} onOpenChange={(value) => { setCreateOpen(value); if (!value) create.reset() }}><DialogContent><DialogTitle>Create assertion key</DialogTitle><DialogDescription>A new HS256 secret will be displayed once after creation.</DialogDescription><form className="mt-5 space-y-4" onSubmit={form.handleSubmit((values) => create.mutate(values))}>{create.isError && <ErrorNotice error={create.error} />}<div><label className="field-label" htmlFor="assertion-key-kid">Key ID (kid)</label><Input id="assertion-key-kid" {...form.register("kid")} />{form.formState.errors.kid && <p className="field-error">{form.formState.errors.kid.message}</p>}</div><ExpiryField id="assertion-key-expiry" register={form.register("expiresAt")} /><div className="flex justify-end gap-3"><Button type="button" variant="outline" onClick={() => setCreateOpen(false)}>Cancel</Button><Button type="submit" disabled={create.isPending}>{create.isPending ? "Creating…" : "Create assertion key"}</Button></div></form></DialogContent></Dialog>
    <ConfirmDialog open={Boolean(revoke)} onOpenChange={(value) => !value && setRevoke(null)} title="Revoke assertion key?" description={`Assertions signed with ${revoke?.kid ?? "this key"} will no longer be accepted. This cannot be undone.`} confirmLabel="Revoke key" pending={revokeMutation.isPending} onConfirm={() => revoke && revokeMutation.mutate(revoke.id)} />
    <SecretRevealDialog open={secret !== null} label="assertion secret" secret={secret ?? ""} onClose={() => { setSecret(null); create.reset() }} />
  </CardContent></Card>
}

function ExpiryField({ id, register }: { id: string; register: ReturnType<ReturnType<typeof useForm<ApiKeyValues>>["register"]> }) {
  return <div><label className="field-label" htmlFor={id}>Expires at (optional)</label><Input id={id} type="datetime-local" {...register} /><p className="field-help">Interpreted in your local time and sent as an ISO timestamp.</p></div>
}

function CredentialList<T>({ rows, pending, error, hasMore, loadingMore, loadMore, render }: { rows: T[]; pending: boolean; error: unknown; hasMore: boolean; loadingMore: boolean; loadMore: () => void; render: (item: T) => React.ReactNode }) {
  if (pending) return <p className="py-8 text-center text-sm text-muted-foreground">Loading credentials…</p>
  if (error) return <ErrorNotice error={error} />
  if (!rows.length) return <div className="rounded-md border border-dashed py-10 text-center text-sm text-muted-foreground">No credentials have been created.</div>
  return <><div className="divide-y rounded-md border">{rows.map(render)}</div>{hasMore && <div className="mt-4 text-center"><Button variant="outline" size="sm" onClick={loadMore} disabled={loadingMore}>{loadingMore ? "Loading…" : "Load more"}</Button></div>}</>
}

function CredentialRow({ title, subtitle, status, expiresAt, createdAt, revokedAt, onRevoke }: { title: string; subtitle: string; status: string; expiresAt: string | null; createdAt: string; revokedAt: string | null; onRevoke: () => void }) {
  return <div className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between"><div className="min-w-0"><div className="flex items-center gap-2"><p className="truncate font-medium">{title}</p><Badge>{status}</Badge></div><p className="mt-1 text-xs text-muted-foreground">{subtitle} · Created {formatDate(createdAt)}</p><p className="mt-1 text-xs text-muted-foreground">{revokedAt ? `Revoked ${formatDate(revokedAt)}` : `Expires ${formatDate(expiresAt)}`}</p></div>{!revokedAt && status !== "revoked" && <Button className="shrink-0" variant="outline" size="sm" onClick={onRevoke}>Revoke</Button>}</div>
}
