# Agent Smith Admin UI

Standalone React/Vite admin console. It is intentionally independent from the fake HRIS
client in `clients/web` and uses only relative Admin HTTP URLs.

## Development

From the repository root, apply migrations, bootstrap the first operator, and start
Admin HTTP on port `8766`:

```bash
poetry run alembic upgrade head
poetry run python -m agent_smith.admin.cli bootstrap-admin
poetry run python -m agent_smith.transports.admin_http.main
```

Then, from this directory:

```bash
npm ci
npm run dev
```

Vite listens on `http://127.0.0.1:5174` and proxies `/auth`, `/api`, and `/health` to
`http://127.0.0.1:8766`.

## Verification and production build

```bash
npm run lint
npm run typecheck
npm run test
npm run build
npm run test:e2e
```

The build output is `dist/`. Set `AGENT_SMITH_ADMIN_UI_DIST` to its absolute or
process-relative path when starting Admin HTTP for same-origin production delivery.
The backend validates the directory and `index.html` at startup.

```bash
npm run build
cd ..
AGENT_SMITH_ADMIN_UI_DIST=admin-ui/dist \
  poetry run python -m agent_smith.transports.admin_http.main
```

Leave `AGENT_SMITH_ADMIN_UI_DIST` unset when Vite serves the UI in development. In
production, Admin HTTP serves SPA routes and hashed assets itself; API, auth, health,
and documentation paths never use SPA fallback. HTML is not cached, hashed assets are
immutable, and UI responses include the production security-header policy.
