# Docker

Docker assets for Agent Smith.

This folder owns Docker-related files for local development and service
packaging. Keep compose entrypoints, compose fragments, and Dockerfiles here.

## Layout

```text
docker/
  compose.yml             # default local dependency entrypoint
  compose/
    dependencies/       # Postgres, Redis, vector DBs, search, brokers
      compose.postgres.yml
  Dockerfile.server     # future HTTP/API server image
  Dockerfile.worker     # future worker image
```

Dependency compose files are intentionally limited to external services such as
Postgres, Redis, vector databases, search engines, and message brokers. They are
not the Agent Smith application runtime itself.

## Default Local Stack

The default Docker compose entrypoint includes the Postgres dependency stack:

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

## Adding More Dependencies

Prefer one compose file per dependency family:

```text
docker/compose/dependencies/
  compose.postgres.yml
  compose.redis.yml
  compose.qdrant.yml
  compose.elasticsearch.yml
  compose.rabbitmq.yml
  compose.kafka.yml
```

When a dependency is optional, put it in its own file and document:

- service name
- exposed ports
- expected `.env` variables
- persistent volumes
- reset command
- which Agent Smith feature uses it

## Resetting Data

Postgres data is stored in the `agent_smith_pg_data` Docker volume.

```bash
docker compose -f docker/compose.yml down
docker volume rm agent-smith_agent_smith_pg_data
```
