# Identity, Auth, Policy

## Core Auth Decision

Day la phan nen thiet ke nhu mot tru cot cua harness, khong phai feature phu.
Voi enterprise agent, auth khong chi de login.
No quyet dinh:

- context nao duoc dua vao model;
- capability nao duoc activate;
- tool nao duoc goi;
- action nao can approval;
- du lieu nao phai redact;
- audit log gan voi actor nao.

Khong nen chon huong Smith tu quan ly password/user auth nhu mot app doc lap.
Lam vay se tao them mot user silo moi, dung voi van de cong ty dang co.

Cung khong nen chon mot app hien co nhu SAP/internal app lam source of truth cho Smith.
Trong moi truong co nhieu he thong users lung tung, viec muon mot app dai dien cho toan bo identity la khong vung.

Huong nen chon:

```text
Smith owns authorization.
Smith owns identity graph for agent usage.
Smith does not own human password authentication.
```

Noi cach khac:

```text
Authentication = external identity assertion
Identity normalization = Smith Identity Graph
Authorization = Smith Policy Engine
```

## Smith Identity Graph

Smith can mot canonical principal rieng cho runtime:

```text
Principal
  - human user
  - service account
  - agent instance
  - subagent
  - system job
```

Moi principal co the map toi nhieu external identities:

```text
smith_user_id
  -> company email / SSO subject
  -> SAP account
  -> HR app user id
  -> finance app user id
  -> ticketing app user id
```

Nhung mapping nay la facts duoc verify, khong phai permission mac dinh.
Khong auto merge dua tren email mot cach mu quang.
Neu mapping khong chac chan thi deny by default va yeu cau admin/human verify.

Schema co the bat dau bang:

```text
principals
external_identities
identity_links
groups
group_memberships
role_bindings
capability_grants
resource_grants
delegations
approval_authorities
policy_decisions
```

## External Identities

OAuth/login chi chung minh user kiem soat mot external identity.
No khong tu chung minh external identity do thuoc principal nao.

Smith can workflow de link identity:

- invitation/onboarding;
- user self-link account;
- admin verify;
- provider sync;
- identity broker.

`identity_link` nen co trang thai:

```text
pending
verified
rejected
conflicted
```

Va nen luu nguon verify:

```text
admin
invitation
self_link
trusted_idp
provider_sync
manual_review
```

Rule nen la:

```text
Neu identity chua tung thay:
  - neu match invitation ro rang -> attach vao principal
  - neu khong -> tao principal moi hoac pending onboarding

Neu identity co cung verified email:
  - chi coi la candidate match
  - khong auto-merge cho quyen cao

Neu user muon link them account:
  - yeu cau chung minh kiem soat account do
  - voi domain nhay cam thi can approval

Neu co conflict:
  - deny by default
  - admin resolve
```

## AuthN: Trust Assertion, Not User Store

Smith nen chap nhan login/identity tu IdP hoac identity broker:

```text
OIDC
SAML
JWT assertion
corporate SSO
Keycloak / Entra / Okta / internal IdP
```

Neu cong ty chua co IdP tot, co the dung Smith-facing identity broker.
Nhung broker nay chi lam nhiem vu xac thuc va phat claims.
Smith khong nen quan ly password cua human users nhu source of truth dai han.

Smith chi can biet:

```text
external_subject
issuer
email/display name
groups/claims neu co
auth_time
assurance_level
```

Sau do Smith map ve `principal_id` noi bo.

## MVP Auth

De test nhanh, Smith co the tam thoi duy tri local user/password.
Nhung phai giu chat nguyen tac:

```text
Local user/password chi la mot auth provider tam thoi.
Khong duoc de no tro thanh identity model chinh cua Smith.
```

Khong nen thiet ke:

```text
users.id = source of truth
sessions.user_id
tool_calls.user_id
permissions.user_id
```

Nen thiet ke:

```text
principals.id = source of truth
sessions.principal_id
tool_calls.principal_id
permissions.principal_id
audit_logs.principal_id
```

Local password chi la mot cach login de lay ra `principal_id`:

```text
principals
  id = principal_123
  type = human
  display_name = Nguyen Van A

external_identities
  id = identity_local_001
  principal_id = principal_123
  provider = smith_local
  subject = "vana"

local_credentials
  external_identity_id = identity_local_001
  password_hash = ...
```

Sau nay khi co SSO/OAuth that:

```text
external_identities
  id = identity_sso_888
  principal_id = principal_123
  provider = company_sso
  subject = "abc-xyz"
```

Khong can migrate session/task/audit neu moi thu da bam vao `principal_id`.
Local password co the giu cho dev/admin hoac disable dan.

## MVP Authorization

Tam thoi chi co mot quyen nhu nhau de test la duoc.
Nhung khong nen hardcode lung tung trong code nghiep vu.

Nen co interface policy ngay tu dau:

```python
authorize(principal_id, action, resource, context) -> Decision
```

MVP implementation co the rat don gian:

```text
Neu authenticated principal -> allow
Neu action system/admin -> deny hoac require internal flag
```

Nhung cac diem quan trong van phai di qua policy/hook boundary:

```text
before_context_build
before_capability_search
before_capability_load
before_tool_call
before_provider_request
after_tool_result
```

Nhu vay ve sau them role, group, workspace, approval, data classification se khong phai dap lai kien truc.

Nguyen tac:

```text
MVP auth can be simple.
Identity architecture must not be temporary.
```

## AuthZ: Smith Policy Engine

Authorization cho agent capabilities phai nam trong Smith.
Khong duoc ngam hieu rang user co quyen o SAP thi agent duoc lam moi thu tuong ung.

Policy nen dua tren:

```text
principal
workspace/project
role/group
capability
resource type
resource id/scope
operation: read | draft | execute | approve | admin
side_effect_level
data_classification
session/task context
approval state
```

Smith policy engine quyet dinh:

- co inject capability nay vao context khong;
- co cho `search_capabilities` tra ve tool nay khong;
- co cho tool call di tiep khong;
- co can approval khong;
- ai duoc approve;
- ket qua tool co can redact khong.

## Provider Boundary

Khi goi domain provider/MCP:

```text
Smith
  -> enforce Smith policy
  -> attach actor/delegation metadata
  -> call provider
```

Provider khong nen tu quyet dinh agent co duoc lam viec nguy hiem hay khong neu Smith chua approve.
Provider van co the enforce rule nghiep vu rieng cua no, nhung Smith la lop authorization cho agent runtime.

Neu downstream app ho tro on-behalf-of token thi provider dung delegated token.
Neu downstream app chi co service account/API key thi provider dung service account, nhung phai nhan `actor`, `reason`, `approval_id`, `task_id`, va ghi audit.
Neu app co local account rieng thi mapping local account nam trong provider boundary, khong nam trong reasoning cua model.

## Hooks Cho Auth

Auth can hooks manh:

```text
before_context_build
  -> filter memory/context theo principal va workspace

before_capability_search
  -> chi search trong capability ma user/session co the thay

before_capability_load
  -> chi inject schemas duoc phep

before_tool_call
  -> policy decision, risk scoring, approval gate

approval_required
  -> tim dung approver theo authority graph

before_provider_request
  -> attach actor/delegation metadata, strip raw secrets

after_tool_result
  -> redact theo data classification

audit_event
  -> ghi actor, agent, provider, resource, decision
```

Model khong nen thay raw token, raw policy rules phuc tap, hay secrets.
Model chi nen thay context da duoc authorize va tool da duoc activate.

## Ket Luan Auth

Quyet dinh auth nen la:

```text
No long-term self-managed human passwords.
No app-specific auth as source of truth.
Smith Identity Graph for normalized principals.
Smith Policy Engine for agent authorization.
External IdP/broker only for authentication assertions.
MVP local password is only a temporary auth provider.
```
