# Parent App Integration

Agent Smith V1 expects parent application backends to own user login and call
Smith with a signed app assertion. Smith resolves an app-scoped principal and
streams normalized run events back to the parent backend.

Design background: [Identity And Trusted App Assertions](IDENTITY_TRUSTED_ASSERTIONS.md).

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
    "agentName": "workplace_assistant",
    "modelKey": "openai"
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
  "version": "2026-07-07",
  "event": "message.delta",
  "runId": "run_uuid",
  "sessionId": "smith_session_uuid",
  "sequence": 12,
  "createdAt": "2026-07-07T10:00:00Z",
  "data": {}
}
```

Parent backends should handle at least:

- `session.resolved`
- `message.delta`
- `run.completed`
- `run.failed`

Unknown events should be ignored unless the parent app explicitly opts into
passing them through to its frontend.

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
      payload: { prompt: req.body.prompt, agentName: req.body.agentName },
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
