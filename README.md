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
# OpenAI (requires OPENAI_API_KEY in .env)
poetry run python examples/demo_ai.py --provider openai

# Google Gemini via Vertex (service account in .gcp/ or GEMINI_API_KEY)
poetry run python examples/demo_ai.py --provider google

# Both
poetry run python examples/demo_ai.py --provider all
```

## Tests

```powershell
poetry run pytest
```
