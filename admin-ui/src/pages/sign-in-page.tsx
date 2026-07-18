import { zodResolver } from "@hookform/resolvers/zod"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { LockKeyhole, ShieldCheck } from "lucide-react"
import { useForm } from "react-hook-form"
import { Navigate, useNavigate } from "react-router-dom"
import { z } from "zod"
import { apiRequest } from "../lib/api"
import { sessionQuery } from "../lib/queries"
import { signInSchema } from "../lib/schemas"
import type { SessionResponse } from "../lib/types"
import { ErrorNotice } from "../components/error-notice"
import { Button } from "../components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../components/ui/card"
import { Input } from "../components/ui/input"
import { ThemeToggle } from "../components/theme-toggle"

type Values = z.infer<typeof signInSchema>

export function SignInPage() {
  const session = useQuery(sessionQuery)
  const client = useQueryClient()
  const navigate = useNavigate()
  const form = useForm<Values>({ resolver: zodResolver(signInSchema), defaultValues: { username: "", password: "" } })
  const mutation = useMutation({
    mutationFn: (values: Values) => apiRequest<SessionResponse>("/auth/sign-in", { method: "POST", csrf: false, body: JSON.stringify(values), redirectOnUnauthorized: false }),
    onSuccess: (data) => { client.setQueryData(sessionQuery.queryKey, data); form.reset(); navigate("/providers", { replace: true }) },
  })
  if (session.isSuccess) return <Navigate to="/providers" replace />
  return <main className="auth-background relative grid min-h-screen place-items-center p-4">
    <ThemeToggle className="absolute right-4 top-4 bg-card/70 shadow-sm backdrop-blur sm:right-6 sm:top-6" />
    <Card className="w-full max-w-md shadow-xl">
      <CardHeader className="pb-4"><div className="mb-4 flex h-11 w-11 items-center justify-center rounded-lg bg-slate-950 text-white dark:bg-blue-500"><ShieldCheck className="h-6 w-6" /></div><CardTitle className="text-2xl">Sign in to Agent Smith</CardTitle><CardDescription>Use your admin operator credentials to access the control plane.</CardDescription></CardHeader>
      <CardContent><form className="space-y-4" onSubmit={form.handleSubmit((values) => mutation.mutate(values))} noValidate>
        {mutation.isError && <ErrorNotice error={mutation.error} title="Sign-in failed" />}
        <div><label className="field-label" htmlFor="username">Username</label><Input id="username" autoComplete="username" autoFocus {...form.register("username")} />{form.formState.errors.username && <p className="field-error">{form.formState.errors.username.message}</p>}</div>
        <div><label className="field-label" htmlFor="password">Password</label><Input id="password" type="password" autoComplete="current-password" {...form.register("password")} />{form.formState.errors.password && <p className="field-error">{form.formState.errors.password.message}</p>}</div>
        <Button className="w-full" type="submit" disabled={mutation.isPending}><LockKeyhole className="h-4 w-4" />{mutation.isPending ? "Signing in…" : "Sign in"}</Button>
      </form></CardContent>
    </Card>
  </main>
}
