# Fake HRIS Parent-App Client

Local development client for testing Agent Smith as if it were called by a
parent HRIS-backed app. The UI is served by a small Python stdlib server, and
that server signs a dev HS256 app assertion before relaying requests to Agent
Smith.

This is intentionally not served by Agent Smith. It runs beside the HTTP
adapter and calls the existing `/api/agent/invoke/stream` integration route.

## Configure Agent Smith

Add the trusted app config from `.env.example` to your local `.env`:

```env
AGENT_SMITH_ASSERTION_AUDIENCE=agent-smith
AGENT_SMITH_TRUSTED_APPS_JSON={"hris-sandbox":{"alg":"HS256","keys":{"dev-v1":"dev-secret-change-me"},"allowedProviders":["hris-sandbox"]}}
```

The secret above is for local development only.

## Run

From the repo root:

```bash
docker compose -f docker/compose.yml up -d
poetry run alembic upgrade head
poetry run python -m agent_smith.transports.http.main
```

In another terminal:

```bash
poetry run python clients/web/server.py
```

Open:

```text
http://127.0.0.1:5173
```

## Smoke Test

Pick a fake HRIS user, send:

```text
Reply with exactly: pong
```

The event panel should show `run.started`, `session.resolved`,
`message.delta`, and `run.completed`. A second prompt should reuse the same
Smith session until you click `New session`.

You can also exercise the relay with curl:

```bash
curl -N \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"Reply with exactly: pong","modelKey":"gpt-5.5","user":{"id":"hris-employee-001","employeeId":"E001","displayName":"Nguyen Van An","email":"an.nguyen@example.test","department":"Engineering","roles":["employee"]}}' \
  http://127.0.0.1:5173/api/oneai/chat/stream
```

## Environment

Client server overrides:

| Variable | Default |
| --- | --- |
| `HRIS_SANDBOX_CLIENT_HOST` | `127.0.0.1` |
| `HRIS_SANDBOX_CLIENT_PORT` | `5173` |
| `AGENT_SMITH_URL` | `http://127.0.0.1:8765` |
| `HRIS_SANDBOX_ASSERTION_ISSUER` | `hris-sandbox` |
| `HRIS_SANDBOX_ASSERTION_KEY_ID` | `dev-v1` |
| `HRIS_SANDBOX_ASSERTION_SECRET` | `dev-secret-change-me` |
| `HRIS_SANDBOX_AGENT_NAME` | `test_assistant` |
