# Product, Principles, Stack

## Huong San Pham

Agent Smith duoc dinh huong la mot enterprise agent runtime, khong phai mot personal computer-use agent.

No nen la mot "nhan vien so" biet hanh xu trong moi truong to chuc:

- hieu session, context, task, approval, audit;
- biet hoi lai khi thieu thong tin;
- biet draft truoc khi execute;
- biet escalate/handoff cho con nguoi;
- chi hanh dong qua cac capability da duoc cap quyen.

Agent Smith khong nen la mot agent co quyen bam UI, chay shell, query DB, hay thao tac raw system mot cach rong rai.

## Core Principle

Ranh gioi kien truc quan trong nhat:

```text
Agent Smith Core
  = agent runtime / orchestration / policy / session / task

Business Capability Providers
  = domain tools / business rules / system integrations
```

Agent Smith khong nen chua nghiep vu finance, procurement, HR, SAP, CRM.
Nhung thu do nen nam ngoai core, trong cac provider rieng.

Mot cach noi ngan:

```text
Smith la operating system cua agent.
MCP/domain providers la apps/services cua doanh nghiep.
```

## Python La Ngon Ngu Chinh

Da chot huong Python cho Agent Smith.

Ly do:

- hop voi AI/data/integration/ETL;
- ecosystem tot cho FastAPI, Pydantic, SQLAlchemy, async IO;
- de noi voi Postgres, SAP API, internal APIs, workers;
- phu hop enterprise integration hon Go trong giai doan nay;
- bot cam giac chan khi phai tiep tuc viet TS qua nhieu nam.

Go co the dung sau nay cho execution daemon, sandbox, process supervisor, remote worker.
TS co the dung cho UI/CLI/client SDK neu can, nhung khong phai core backend.

Stack de xuat:

```text
Python
FastAPI
Pydantic
SQLAlchemy + Alembic
Postgres
Task runtime: Temporal / Dramatiq / Celery
Optional: pgvector / search service
```
