# Docker

Docker assets for Agent Smith.

This folder owns Docker-related files for local development and service
packaging. Keep compose entrypoints, compose fragments, and Dockerfiles here.

## Layout

```text
docker/
  compose.yml             # default local dependency entrypoint
  compose/
    dependencies/       # external deps (Postgres and MinIO today)
      compose.postgres.yml
  Dockerfile.server     # reserved HTTP/API server image location
  Dockerfile.worker     # reserved document-worker image location
```

Dependency compose files are intentionally limited to external services such as
Postgres (and later caches/search if needed). They are not the Agent Smith
application runtime itself. Agent invocation remains HTTP/SSE in-process. The
document worker uses Postgres as its durable queue, so no message bus is wired.

## Default Local Stack

The default Docker compose entrypoint includes Postgres and a private MinIO
bucket for local S3-compatible file storage:

```bash
docker compose -f docker/compose.yml up -d
```

For direct usage:

```bash
docker compose -f docker/compose/dependencies/compose.postgres.yml up -d
```

## Current Services

| Service | File | Ports | Purpose |
|---------|------|-------|---------|
| Postgres | `compose.postgres.yml` | `5432` | Control plane database for sessions, resources, tasks, and migrations |
| MinIO | `compose.minio.yml` | `9000`, `9001` | S3 API and local object-storage console |

The `minio-init` one-shot service creates the private `agent-smith` bucket.
It also applies `minio-cors.json` for the documented localhost dev origins. For
browser direct uploads in other environments, configure bucket CORS for the
exact web origins used by your deployment and allow `PUT`, `GET`, `HEAD` plus the
`Content-Type`/`x-amz-checksum-sha256` request headers. Do not use `*` origins in
production and never make the bucket public. Production R2 setup and token
rotation are documented in [R2_STORAGE_OPERATIONS.md](../docs/R2_STORAGE_OPERATIONS.md).

After starting dependencies and applying migrations, run the HTTP service and
document worker as separate processes:

```bash
poetry run python -m agent_smith.transports.http.main
poetry run python -m agent_smith.workers.document_processing.main
```

## Adding More Dependencies

Prefer one compose file per dependency family:

```text
docker/compose/dependencies/
  compose.postgres.yml
  compose.minio.yml
  compose.redis.yml
  compose.qdrant.yml
  compose.elasticsearch.yml
```

When a dependency is optional, put it in its own file and document:

- service name
- exposed ports
- expected `.env` variables
- persistent volumes
- reset command
- which Agent Smith feature uses it

## Resetting Data

Postgres and MinIO data are stored in Docker volumes.

```bash
docker compose -f docker/compose.yml down
docker volume rm agent-smith_agent_smith_pg_data
docker volume rm agent-smith_agent_smith_minio_data
```
