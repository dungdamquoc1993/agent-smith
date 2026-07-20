# Parent App Integration

Agent Smith V1 expects parent application backends to own user login and call
Smith with a signed app assertion. Smith resolves an app-scoped principal and
streams normalized run events back to the parent backend.

Design background: [Identity And Trusted App Assertions](IDENTITY_TRUSTED_ASSERTIONS.md).

## Running Smith HTTP

The HTTP transport is a FastAPI app served by uvicorn:

```bash
poetry run python -m agent_smith.transports.runtime_http.main
```

or:

```bash
poetry run uvicorn agent_smith.transports.runtime_http.main:app --host 127.0.0.1 --port 8765
```

FastAPI docs are enabled by default at `/docs` and `/openapi.json`. Set
`AGENT_SMITH_HTTP_DOCS_ENABLED=false` to disable them for deployed environments.

## Admin Setup

Provider onboarding is handled by the standalone Admin HTTP process. Bootstrap the
first operator and start the process after configuring the assertion-secret encryption
key:

```bash
poetry run alembic upgrade head
poetry run python -m agent_smith.admin.cli bootstrap-admin
AGENT_SMITH_IDENTITY_SECRETS_KEY=<fernet-key> \
  poetry run python -m agent_smith.transports.admin_http.main
```

For interactive onboarding, run the standalone UI from `admin-ui/` on port `5174`.
The HTTP examples below describe the same contract used by that UI.

Create the provider:

```http
POST /api/identity-providers
Cookie: <admin-session-cookie>
Origin: <configured-admin-public-origin>
X-CSRF-Token: <csrf-cookie-value>
Content-Type: application/json
```

```json
{
  "slug": "adw",
  "issuer": "adw",
  "displayName": "ADW"
}
```

Create a Provider API key:

```http
POST /api/identity-providers/{providerId}/api-keys
Content-Type: application/json
```

```json
{ "name": "runtime-v1" }
```

The response includes `apiKey.rawKey` exactly once. Smith stores only a hash and
prefix after that.

Create an assertion signing key:

```http
POST /api/identity-providers/{providerId}/assertion-keys
Content-Type: application/json
```

```json
{ "kid": "v1" }
```

The response includes `assertionKey.rawSecret` exactly once. Smith stores the
secret encrypted at rest and uses `kid` to select it during assertion
verification.

## Invoke Stream

```http
POST /api/agent/invoke/stream
X-Agent-Smith-Provider-Key: <provider-api-key>
Authorization: Bearer <signed-app-assertion>
Content-Type: application/json
Accept: text/event-stream
```

Body:

```json
{
  "payload": {
    "prompt": "Xin chào",
    "attachments": [
      {"fileId": "managed-file-uuid"}
    ],
    "agentName": "workplace_assistant",
    "modelKey": "gpt-5.5"
  },
  "session": {
    "smithSessionId": null,
    "externalSessionId": "adw-conversation-id"
  },
  "surface": {
    "app": "adw",
    "route": "/oneai",
    "origin": "https://adw.example",
    "locale": "vi-VN",
    "timezone": "Asia/Ho_Chi_Minh",
    "userAgent": "browser ua"
  },
  "metadata": {
    "workspaceId": "optional"
  },
  "correlationId": "trace-from-parent"
}
```

## Managed Attachments

Upload the binary through Smith's managed file API before invoking the agent.
The parent backend authenticates each file request with the same provider API key
and signed assertion used for invocation; the browser only receives a short-lived
presigned `PUT` URL and never receives S3/R2 credentials.

```text
POST /api/files/uploads
PUT <presigned upload URL>
POST /api/files/{fileId}/complete
GET /api/files/{fileId}               # poll until ready/failed
POST /api/agent/invoke/stream
```

Images become `ready` during completion. Documents are atomically enqueued and
move through `uploaded -> processing -> ready|failed` in the dedicated worker.
After the file is `ready`, pass only its `fileId` in
`payload.attachments`. Smith resolves ownership and metadata server-side; callers
must not send MIME type, filename, binary, base64, remote URLs, or provider asset
IDs in the invoke request.

- Supported prompt images: PNG, JPEG, GIF, and WebP.
- Supported documents: UTF-8 TXT, Markdown, CSV, text-layer PDF, DOCX, and XLSX.
- Legacy DOC/XLS are rejected with 415. Scanned PDFs, encrypted documents, and
  corrupt documents retain their original but finish as `failed` in this MVP.
- At most 8 attachments per invocation and 20 MiB of total raw image bytes by
  default. Both limits are server configuration.
- `prompt` may be an empty string when `attachments` is non-empty. At least one
  of them is required.
- The selected model must advertise image input support.
- Attachments are immutable session references. Smith reads and base64-encodes
  the private object only while preparing a provider request; neither Postgres
  nor the session payload stores image bytes.
- Document processors persist normalized text/table/chunk derivatives privately.
  The prompt resolver emits bounded text with file/page/sheet provenance; it
  never sends the original document URL or binary to an LLM provider.

The file response exposes worker state without another endpoint:

```json
{
  "file": {
    "status": "processing",
    "detectedMimeType": "application/pdf",
    "processing": {
      "jobId": "job-uuid",
      "status": "running",
      "phase": "extracting",
      "progressPercent": 30,
      "attempts": 1,
      "maxAttempts": 5,
      "processor": "pypdf_text:1",
      "error": null
    }
  }
}
```

Polling clients should stop on file status `ready` or `failed`. The original is
still downloadable when status is `failed`; only use as an LLM attachment is
blocked.

Validation occurs before Smith opens the SSE stream. Relevant responses are:

| Status | Error code | Meaning |
|---:|---|---|
| 400 | `invalid_attachments`, `duplicate_attachment`, `too_many_attachments`, `model_does_not_support_images` | Invalid attachment shape/count or text-only model |
| 404 | `attachment_not_found` | Missing, deleted, or cross-principal file |
| 409 | `attachment_not_ready`, `attachment_processing_failed` | Upload/processing is incomplete or failed |
| 413 | `attachments_too_large`, `attachment_context_budget_exhausted` | Image bytes or document context exceed the configured budget |
| 415 | `unsupported_attachment_type`, `unsupported_file_type` | MIME type is outside the supported image/document set |

For the local/test SSE route, the equivalent shape is root-level:

```json
{
  "prompt": "Describe this image",
  "attachments": [{"fileId": "managed-file-uuid"}]
}
```

See [Managed File Storage Implementation Plan](FILE_STORAGE_IMPLEMENTATION_PLAN.md)
for the storage lifecycle and retention behavior.

## Signed Assertion

V1 supports HS256 compact JWS. The assertion must include:

```json
{
  "iss": "adw",
  "aud": "agent-smith",
  "sub": "adw-user-uuid",
  "jti": "unique-request-id",
  "iat": 1783420000,
  "exp": 1783420300,
  "actor": {
    "displayName": "Nguyen Van A",
    "email": "a@company.vn",
    "roles": ["manager"],
    "department": "IT",
    "upstreamAuth": {
      "provider": "hris",
      "subject": "vana",
      "assurance": "asserted_by_adw"
    }
  }
}
```

Smith resolves the provider from `X-Agent-Smith-Provider-Key`, then creates or
resolves `external_identity(identity_provider_id=<provider>, subject="adw-user-uuid")`.
`upstreamAuth` is context/provenance only; it does not create an HRIS identity.

## Stream Events

Each SSE event name matches `data.event`. Event data:

```json
{
  "version": "2026-07-20",
  "event": "message.delta",
  "runId": "run_uuid",
  "sessionId": "smith_session_uuid",
  "sequence": 12,
  "createdAt": "2026-07-20T10:00:00Z",
  "data": {}
}
```

Parent backends should handle at least:

- `run.started`
- `session.resolved`
- `message.delta`
- `usage.updated`
- `run.completed`
- `run.failed`

Version `2026-07-20` changes `usage` from the final provider call to the aggregate
usage and cost of every normal turn, tool-loop turn, and compaction call in the
run. `usage.updated` has this terminal shape:

```json
{
  "usage": {"input": 120, "output": 24, "totalTokens": 144, "cost": {"total": 0.01}},
  "callCount": 3,
  "recording": {"status": "complete"}
}
```

`run.completed.data` repeats the aggregate `usage`, `callCount`, and `recording`.
`run.failed.data` includes the same partial aggregates plus `code`, a public-safe
`message`, `retryable`, and `stage`. Exactly one `run.completed` or `run.failed`
terminates a connected stream. `run.started` is emitted only after the run record
has been persisted.

Execution status and recording status are independent: a successful response may
have `recording.status = "degraded"` when a non-critical finalize/link write failed.
The parent should return the successful answer and separately surface or monitor
the telemetry degradation; it must not retry the provider request solely because
recording is degraded.

Parent clients must ignore unknown fields and events unless they explicitly opt
into passing them through to the frontend. This keeps clients forward-compatible
with later stream versions.

The legacy `/api/prompt/stream` route keeps `session`, `done`, and `error` event
names. Its `done` payload now also contains `runId`, aggregate `usage`, `callCount`,
and `recording.status`.

## Express Relay Sketch

```js
app.post('/api/oneai/chat/stream', authenticate, async (req, res) => {
  const assertion = signSmithAssertion(req.user);
  const smith = await fetch(`${SMITH_URL}/api/agent/invoke/stream`, {
    method: 'POST',
    headers: {
      'X-Agent-Smith-Provider-Key': process.env.SMITH_PROVIDER_API_KEY,
      Authorization: `Bearer ${assertion}`,
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
    },
    body: JSON.stringify({
      payload: {
        prompt: req.body.prompt ?? '',
        attachments: req.body.attachments ?? [],
        agentName: req.body.agentName,
      },
      session: { smithSessionId: req.body.smithSessionId, externalSessionId: req.body.conversationId },
      surface: {
        app: 'adw',
        route: req.body.route,
        locale: req.body.locale,
        timezone: req.body.timezone,
        userAgent: req.get('user-agent'),
      },
      correlationId: req.id,
    }),
  });

  res.writeHead(smith.status, {
    'content-type': smith.headers.get('content-type') || 'text/event-stream; charset=utf-8',
    'cache-control': 'no-cache',
  });
  smith.body.pipe(res);
});
```
