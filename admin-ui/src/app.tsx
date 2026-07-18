import { useEffect } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Navigate, Outlet, Route, Routes, useLocation, useNavigate } from "react-router-dom"
import { ApiError, setUnauthorizedHandler } from "./lib/api"
import { sessionQuery } from "./lib/queries"
import { LoadingState } from "./components/loading-state"
import { AppShell } from "./components/app-shell"
import { SignInPage } from "./pages/sign-in-page"
import { ProvidersPage } from "./pages/providers-page"
import { ProviderDetailPage } from "./pages/provider-detail-page"
import { AuditPage } from "./pages/audit-page"
import { NotFoundPage } from "./pages/not-found-page"

function UnauthorizedBridge() {
  const client = useQueryClient()
  const navigate = useNavigate()
  useEffect(() => {
    setUnauthorizedHandler(() => {
      client.clear()
      navigate("/sign-in", { replace: true })
    })
    return () => setUnauthorizedHandler(undefined)
  }, [client, navigate])
  return null
}

function AuthenticatedLayout() {
  const location = useLocation()
  const session = useQuery(sessionQuery)
  if (session.isPending) return <div className="min-h-screen bg-background"><LoadingState label="Checking your session…" /></div>
  if (session.error instanceof ApiError && session.error.status === 401) return <Navigate to="/sign-in" replace state={{ from: location }} />
  if (session.isError) return <Navigate to="/sign-in" replace />
  return <AppShell operator={session.data.operator}><Outlet /></AppShell>
}

export function App() {
  return <><UnauthorizedBridge /><Routes>
    <Route path="/sign-in" element={<SignInPage />} />
    <Route element={<AuthenticatedLayout />}>
      <Route index element={<Navigate to="/providers" replace />} />
      <Route path="/providers" element={<ProvidersPage />} />
      <Route path="/providers/:providerId" element={<ProviderDetailPage />} />
      <Route path="/audit" element={<AuditPage />} />
    </Route>
    <Route path="*" element={<NotFoundPage />} />
  </Routes></>
}
