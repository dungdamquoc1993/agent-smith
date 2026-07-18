import { useState } from "react"
import { useInfiniteQuery } from "@tanstack/react-query"
import { Filter, RotateCcw, ScrollText } from "lucide-react"
import { apiRequest } from "../lib/api"
import type { AuditEvent, CursorPage } from "../lib/types"
import { formatDate } from "../lib/utils"
import { ErrorNotice } from "../components/error-notice"
import { LoadingState } from "../components/loading-state"
import { Badge } from "../components/ui/badge"
import { Button } from "../components/ui/button"
import { Card, CardContent } from "../components/ui/card"
import { Input } from "../components/ui/input"

type Filters = { action: string; outcome: string; actorOperatorId: string; resourceType: string; resourceId: string }
const emptyFilters: Filters = { action: "", outcome: "", actorOperatorId: "", resourceType: "", resourceId: "" }

export function AuditPage() {
  const [draft, setDraft] = useState<Filters>(emptyFilters)
  const [filters, setFilters] = useState<Filters>(emptyFilters)
  const query = useInfiniteQuery({
    queryKey: ["audit-events", filters],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) => {
      const params = new URLSearchParams({ limit: "50" })
      if (pageParam) params.set("cursor", pageParam)
      for (const [key, value] of Object.entries(filters)) if (value) params.set(key, value)
      return apiRequest<CursorPage<AuditEvent, "auditEvents">>(`/api/audit-events?${params}`)
    },
    getNextPageParam: (page) => page.nextCursor ?? undefined,
  })
  const rows = query.data?.pages.flatMap((page) => page.auditEvents) ?? []
  function update(key: keyof Filters, value: string) { setDraft((current) => ({ ...current, [key]: value })) }
  return <>
    <div><h1 className="page-title">Audit log</h1><p className="page-subtitle">Review control-plane authentication and configuration events.</p></div>
    <Card className="mt-8"><CardContent className="pt-5 sm:pt-6"><form className="grid gap-4 md:grid-cols-2 xl:grid-cols-5" onSubmit={(event) => { event.preventDefault(); setFilters(draft) }}>
      <AuditField label="Action" id="audit-action"><Input id="audit-action" placeholder="identity_provider.create" value={draft.action} onChange={(event) => update("action", event.target.value)} /></AuditField>
      <AuditField label="Outcome" id="audit-outcome"><select id="audit-outcome" className="h-10 w-full rounded-md border bg-card px-3 text-sm" value={draft.outcome} onChange={(event) => update("outcome", event.target.value)}><option value="">All outcomes</option><option value="success">Success</option><option value="denied">Denied</option><option value="failed">Failed</option></select></AuditField>
      <AuditField label="Actor operator ID" id="audit-actor"><Input id="audit-actor" value={draft.actorOperatorId} onChange={(event) => update("actorOperatorId", event.target.value)} /></AuditField>
      <AuditField label="Resource type" id="audit-resource-type"><Input id="audit-resource-type" value={draft.resourceType} onChange={(event) => update("resourceType", event.target.value)} /></AuditField>
      <AuditField label="Resource ID" id="audit-resource-id"><Input id="audit-resource-id" value={draft.resourceId} onChange={(event) => update("resourceId", event.target.value)} /></AuditField>
      <div className="flex gap-3 md:col-span-2 xl:col-span-5"><Button type="submit"><Filter className="h-4 w-4" />Apply filters</Button><Button type="button" variant="outline" onClick={() => { setDraft(emptyFilters); setFilters(emptyFilters) }}><RotateCcw className="h-4 w-4" />Reset</Button></div>
    </form></CardContent></Card>
    <div className="mt-6">{query.isPending ? <LoadingState label="Loading audit events…" /> : query.isError ? <ErrorNotice error={query.error} /> : !rows.length ? <Card><CardContent className="flex flex-col items-center py-14 text-center"><ScrollText className="h-8 w-8 text-slate-400 dark:text-slate-500" /><h2 className="mt-4 text-lg font-semibold">No audit events found</h2><p className="mt-2 text-sm text-muted-foreground">Try changing the filters or check again after admin activity.</p></CardContent></Card> : <>
      <div className="overflow-hidden rounded-lg border bg-card shadow-card"><div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead className="border-b bg-slate-50 text-xs uppercase tracking-wide text-slate-500 dark:bg-slate-900 dark:text-slate-400"><tr><th className="px-4 py-3 font-medium">Time</th><th className="px-4 py-3 font-medium">Action</th><th className="px-4 py-3 font-medium">Outcome</th><th className="px-4 py-3 font-medium">Actor</th><th className="px-4 py-3 font-medium">Resource</th><th className="px-4 py-3 font-medium">Details</th></tr></thead><tbody className="divide-y">{rows.map((event) => <tr key={event.id} className="align-top"><td className="whitespace-nowrap px-4 py-4 text-xs text-slate-600 dark:text-slate-300">{formatDate(event.occurredAt)}</td><td className="px-4 py-4 font-mono text-xs">{event.action}</td><td className="px-4 py-4"><Badge>{event.outcome}</Badge></td><td className="px-4 py-4"><p className="text-sm">{event.actor.identifier || event.actor.kind}</p>{event.actor.operatorId && <p className="mt-1 font-mono text-xs text-muted-foreground">{event.actor.operatorId}</p>}</td><td className="px-4 py-4"><p>{event.resourceType}</p>{event.resourceId && <p className="mt-1 max-w-52 truncate font-mono text-xs text-muted-foreground" title={event.resourceId}>{event.resourceId}</p>}</td><td className="px-4 py-4"><details><summary className="cursor-pointer text-sm font-medium text-blue-700 dark:text-blue-300">View metadata</summary><pre className="mt-3 max-w-sm overflow-auto rounded-md bg-slate-950 p-3 text-xs text-slate-50">{JSON.stringify({ ...event.metadata, requestId: event.actor.requestId, ipAddress: event.actor.ipAddress }, null, 2)}</pre></details></td></tr>)}</tbody></table></div></div>
      {query.hasNextPage && <div className="mt-5 text-center"><Button variant="outline" onClick={() => query.fetchNextPage()} disabled={query.isFetchingNextPage}>{query.isFetchingNextPage ? "Loading…" : "Load more"}</Button></div>}
    </>}</div>
  </>
}

function AuditField({ label, id, children }: { label: string; id: string; children: React.ReactNode }) { return <div><label className="field-label" htmlFor={id}>{label}</label>{children}</div> }
