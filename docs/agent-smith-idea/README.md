# Agent Smith Idea Notes

Day la checkpoint kien truc sau cac phien brainstorming ve Agent Smith.
Muc tieu cua folder nay la tach cac y lon thanh nhieu file nho de tiep tuc thao luan ma khong bi mot document qua dai.

## Reading Order

1. [Product, Principles, Stack](01-product-principles-stack.md)
2. [Storage, Runtime, Scope](02-storage-runtime-scope.md)
3. [Enterprise Integration](03-enterprise-integration.md)
4. [Capabilities And Tools](04-capabilities-and-tools.md)
5. [Identity, Auth, Policy](05-identity-auth-policy.md)
6. [Architecture, Decisions, Open Questions](06-architecture-decisions-open-questions.md)
7. [Agent Task Runtime Phase 3](07-agent-task-runtime-phase3.md)

## Current Direction

Agent Smith la enterprise agent runtime, khong phai personal computer-use agent.

Core direction:

```text
Agent Smith Core
  = agent runtime / orchestration / policy / session / task / identity

Business Capability Providers
  = domain tools / business rules / system integrations
```

Nhung quyet dinh lon hien tai:

- Python la ngon ngu chinh cho core backend.
- Postgres la control plane chinh.
- Session nen theo append-only/event-tree model hoc tu PI va Claude Code.
- Raw shell/computer use khong nam trong production core.
- Native/base tools chi phuc vu harness/platform.
- Business/domain tools day ra capability providers, uu tien MCP/provider boundary.
- Capability loading phai lazy, khong dump all tool schemas vao context.
- Side-effect actions phai qua draft/validate/approval/execute/audit.
- Smith khong tu bien thanh identity/password silo moi.
- Moi runtime state, audit, session, policy nen bam vao `principal_id`.
