# Architecture, Decisions, Open Questions

## High-Level Architecture

Tong quan:

```text
Agent Smith Runtime
  - agent loop
  - session tree/event log
  - memory/context
  - task runtime
  - hooks
  - identity graph
  - policy/approval/audit
  - capability registry

Native Harness Tools
  - ask_user
  - request_approval
  - task/artifact/memory tools
  - capability search/load

Capability Providers
  - MCP servers
  - HTTP services
  - selected native thin providers

Enterprise Systems
  - SAP
  - internal apps
  - databases
  - documents
  - ticketing/CRM/email

Storage
  - Postgres control plane
  - object storage for artifacts
  - optional JSONL export/debug transcript
```

## Decisions So Far

- Chon Python cho core backend.
- Chon Postgres lam control plane chinh.
- Giu session/event model append-only.
- Khong mac dinh raw filesystem/shell/computer use trong enterprise production.
- Native/base tools chi cho harness/platform.
- Base tools phuc vu toi da user/workspace chung, nhung khong permissionless.
- Nghiep vu day ra MCP/capability providers.
- Capability pack la manifest/contract, khong phai business implementation.
- MCP duoc load lazy qua capability search, khong dump all schemas vao context.
- Side-effect actions phai qua draft/validate/approval/execute/audit.
- Smith khong tu quan ly human passwords nhu source of truth dai han.
- MVP co the co local password provider de test nhanh.
- Moi state quan trong nen bam vao `principal_id`, khong bam vao `users.id`.
- Smith so huu identity graph va authorization cho agent capabilities.
- AuthN den tu external IdP/broker/local provider duoi dang trusted assertion.

## Open Questions

- Thiet ke `CapabilityRegistry` schema cu the trong Postgres the nao?
- Tool search nen dung lexical search, embeddings, hay hybrid?
- Capability activation nen gan voi session, user role, project, hay current task?
- MCP providers noi voi Smith qua direct MCP client hay qua MCP gateway rieng?
- Approval UI se nam trong web app, chat surface, hay external workflow system?
- Memory nen chia scope: user, org, project, domain, session nhu the nao?
- Audit/event schema nen thiet ke tu dau theo compliance nao?
- Task runtime nen chon Temporal, Celery, Dramatiq, hay tu viet nhe truoc?
- Policy engine nen tu viet nhe truoc hay dung OPA/Cedar/Oso ve sau?
- Identity link workflow nen manual approve, HR-sync, hay request-based?
- MVP authorization nen co workspace/role nhe ngay tu dau hay chi authenticated allow?
