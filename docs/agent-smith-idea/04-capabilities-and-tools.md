# Capabilities And Tools

## Native Tools vs MCP Tools

Quyet dinh kien truc:

```text
Native tools = harness/platform tools cua Agent Smith
MCP tools = business/domain/system capabilities
```

Native tools chi nen giu nhung thu rieng cua Smith runtime:

```text
ask_user
request_approval
create_task
update_task
search_capabilities
load_capability
remember
recall
read_artifact
write_artifact
emit_audit_event
handoff_to_human
schedule_background_job
```

## Base Tools

Base tools nen duoc thiet ke nhu "agent OS syscalls".
Tuc la chung phuc vu toi da use cases chung cua tat ca users/workspaces:

- hoi nguoi dung;
- quan ly task;
- quan ly artifacts;
- quan ly memory/context;
- tim va load capabilities;
- xin approval;
- audit;
- handoff;
- schedule/background job;
- doc trang thai session.

Phan mo rong co limit nen chi di theo nghiep vu/domain.
Neu mot tool co y nghia chung cho moi assistant thi no thuoc Smith base tools.
Neu mot tool can hieu finance/procurement/HR/SAP thi no thuoc capability provider.

Base tools cung phai author-aware.
Khong phai vi no la platform tool ma ai cung duoc goi tuy y.

Vi du:

```text
search_capabilities
  -> chi tra ve capabilities ma principal/session co the thay

load_capability
  -> chi inject tool schemas da duoc authorize

remember
  -> chi ghi memory vao scope ma principal co quyen ghi

recall
  -> chi doc memory theo principal/workspace/project/domain scope

read_artifact
  -> check owner/workspace/data classification

write_artifact
  -> check quota, workspace, data classification

create_task / update_task
  -> check actor co quyen tren workspace/task khong

request_approval
  -> tim approver dung theo policy/authority graph

schedule_background_job
  -> check risk, cost, quota, allowed job type

handoff_to_human
  -> check duoc handoff cho ai/channel nao
```

Noi cach khac:

```text
Base tools are universal.
Base tools are not permissionless.
```

## Business Tools

Nhung tool lien quan nghiep vu nen day ra MCP/capability providers:

```text
finance-mcp
procurement-mcp
hr-mcp
sap-mcp
ticketing-mcp
crm-mcp
```

Business logic khong nen nam trong native tool.

Khong nen viet:

```python
def create_payment_request(...):
    # nhieu tram dong finance logic
    # query DB
    # map SAP
    # validate approval rules
```

Nen viet, neu can native wrapper:

```python
def create_payment_request_draft(input):
    return capability_client.call(
        "finance.create_payment_request_draft",
        input,
    )
```

Source of truth cua nghiep vu nam o provider/service, khong nam o Smith.

## MCP Boundary

MCP khong phai business boundary tu than no.
MCP chi la mot provider protocol.

Boundary thuc su la:

```text
Capability Provider Boundary
```

So do:

```text
Agent Smith Core
  -> Capability Registry
  -> Policy / Permission / Approval Hooks
  -> Capability Provider
       -> MCP server
       -> HTTP service
       -> native platform tool
  -> Enterprise Systems
       -> SAP
       -> internal apps
       -> databases
```

Capability pack khong nen la code nghiep vu.
No nen la manifest/contract:

- tool name;
- description;
- input/output schema;
- side effect level;
- permission policy;
- approval requirements;
- audit category;
- examples;
- owner;
- provider reference.

Vi du:

```yaml
name: procurement
provider: mcp
server: procurement-mcp
summary: Purchase orders, vendors, receiving, requisitions
tools:
  - procurement.search_po
  - procurement.create_requisition_draft
  - procurement.check_vendor_status
policies:
  - create_* requires approval
  - vendor bank data requires finance_role
owners:
  - procurement-platform-team
```

Neu sau nay mot capability chuyen tu native sang MCP, Smith khong doi.
Chi doi manifest provider:

```yaml
provider: native
```

sang:

```yaml
provider: mcp
```

mien la contract giu nguyen.

## MCP Token Cost Va Tool Loading

Khong nen load tat ca MCP tool schemas vao context.

Nen dung lazy loading:

```text
1. Smith thay catalog/summary ngan cua capability packs.
2. Smith dung search_capabilities(query).
3. Runtime activate capability pack lien quan.
4. Chi inject 5-15 tool schemas can thiet vao turn hien tai.
```

Smith khong nen co "tat ca tools cua cong ty" trong mot context.
Nen co active workspace/profile:

```text
HR assistant
Finance assistant
Procurement assistant
IT support assistant
Project-specific assistant
```

Cung mot runtime, nhung active capability packs khac nhau.

## Tool Contract Metadata

Moi tool/capability nen co metadata ro rang:

```text
read/write
side_effect_level
requires_approval
idempotency_key
timeout
owner
audit_category
allowed_roles
data_classification
rate_limit
expected_latency
```

Nhung metadata nay duoc dung boi:

- policy hooks;
- approval flow;
- audit;
- UI;
- task runtime;
- tool search;
- risk classifier.

## Draft Before Execute

Voi action co side effect, flow mac dinh nen la:

```text
draft
  -> validate
  -> explain diff / explain consequence
  -> human approval
  -> execute
  -> audit
  -> verify result
```

Vi du:

```text
create_payment_request_draft
validate_payment_request
request_approval
execute_payment_request
verify_payment_request_status
```

Khong de model truc tiep goi action nguy hiem neu chua qua policy/approval.

## Hooks La Xuong Song An Toan

Smith nen co hooks tuong tu PI/Claude Code, nhung thiet ke cho enterprise:

```text
before_agent_start
before_tool_call
after_tool_result
permission_request
approval_required
audit_event
before_provider_request
before_context_build
before_task_start
after_task_end
session_before_compact
```

Hooks dung de:

- chen context;
- chan tool;
- yeu cau approval;
- cap nhat permission;
- redact sensitive data;
- audit;
- enforce policy;
- terminate unsafe flow;
- route sang background task.
