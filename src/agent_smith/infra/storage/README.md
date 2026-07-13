# Storage backends

This package is organized by concrete backend, not by a generic `Database`
abstraction:

```text
storage/
├── postgres/       # transactional persistence (implemented)
│   ├── database.py # engine, pool, async session lifecycle
│   ├── models/     # SQLAlchemy schema
│   └── adapters/   # capability ports implemented with Postgres
├── qdrant/         # vector-index backend placeholder
└── elasticsearch/  # search-index backend placeholder
```

Core and App code define ports by capability (`ResourceStore`,
`SessionStorage`, `SessionCatalog`, identity stores, and future `VectorIndex` /
`SearchIndex`). They do not depend on a vendor-neutral database interface.

Postgres adapters own SQLAlchemy mapping and short database operations. An
adapter may own a transaction when one port operation is inherently atomic.
Application workflows define wider business transaction boundaries; for
example, session fork is implemented as one atomic lifecycle operation rather
than a sequence of independently committed harness writes.

Qdrant and Elasticsearch intentionally contain no client dependency or runtime
implementation yet. Their packages reserve an explicit home for future
backend-specific lifecycle, schema, and adapters.

The boundary is protected by architecture tests: Core cannot import concrete
storage, App services cannot import SQLAlchemy or concrete storage packages,
and SQLAlchemy inside infra is confined to the Postgres backend.
