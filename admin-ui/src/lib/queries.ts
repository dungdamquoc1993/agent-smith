import { apiRequest } from "./api"
import type { SessionResponse } from "./types"

export const sessionQuery = {
  queryKey: ["session"] as const,
  queryFn: () => apiRequest<SessionResponse>("/auth/session", { redirectOnUnauthorized: false }),
  retry: false,
  staleTime: 30_000,
}
