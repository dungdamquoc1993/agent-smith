# Enterprise Integration

## Integration Strategy

Khong nen migrate tat ca legacy systems sang cloud DB ngay.
Do la modernization program lon va de bi no scope.

Huong dung:

```text
SAP / legacy apps / internal DB
  = source of truth hien tai

Agent Smith Postgres
  = control plane + projection + audit + task state
```

Agent doc tu canonical projection/read model khi co the.
Agent ghi qua domain API/capability provider chinh thuc.

Neu app khong co API:

- tao adapter service;
- hoac RPA worker bi nhot ky;
- khong de agent raw-click UI truc tiep.

## Canonical Domain Model

Nen tao canonical domain model:

```text
Employee
Vendor
Customer
Material / SKU
PurchaseOrder
Invoice
Payment
Ticket
Contract
CostCenter
Warehouse
ApprovalRequest
```

Moi adapter map du lieu lung tung tu SAP/internal DB ve model nay.
Day la anti-corruption layer cua he thong.

## Source Of Truth

Smith khong nen co tham vong thay the tat ca source of truth cua cong ty.

Nen coi:

```text
Enterprise systems
  = business source of truth

Smith
  = agent control plane, projection, workflow, audit, policy
```

Viec ghi vao he thong nghiep vu nen di qua capability provider chinh thuc.
Khong de agent query/write raw DB neu khong co provider/policy bao quanh.
