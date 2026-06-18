# Agent Smith

Enterprise agent runtime - Python core with unified AI layer and Postgres control plane.

## Prerequisites

- Python 3.11+
- [Poetry](https://python-poetry.org/docs/#installation)
- Docker (for Postgres)

## Setup

```powershell
# Install Poetry (if not installed)
(Invoke-WebRequest -Uri https://install.python-poetry.org -UseBasicParsing).Content | py -

poetry install
cp .env.example .env
# Edit .env with your OPENAI_API_KEY

docker compose up -d
poetry run alembic upgrade head
```

## Demo unified AI layer

```powershell
# Faux provider (offline, no API key)
poetry run python examples/demo_ai.py --provider faux

# OpenAI live (requires OPENAI_API_KEY in .env)
poetry run python examples/demo_ai.py --provider openai
```

## Tests

```powershell
poetry run pytest
```
