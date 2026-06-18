# Storage, Runtime, Scope

## Storage Va State

Khong nen chi dung filesystem nhu cac coding agent ca nhan.

Voi enterprise agent, Postgres nen la control plane chinh:

- sessions;
- task/subagent status;
- tool calls;
- approvals;
- audit logs;
- connector configs;
- permission policies;
- user/org/project mapping;
- sync jobs;
- indexed entities;
- capability registry.

Tuy nhien van nen hoc append-only/session-tree model tu PI va Claude Code.
Session nen duoc thiet ke nhu event log co the replay/fork/compact.

Filesystem van co vai tro, nhung khong phai source of truth enterprise:

- temp files;
- uploaded artifacts;
- generated reports;
- import/export;
- local caches;
- debug transcript export.

Source of truth nen la:

```text
Postgres + object storage + audit/event log
```

## Filesystem, Shell, Computer Use

Production enterprise agent nen mac dinh:

```text
no raw shell
no raw computer use
no raw database write
no random UI browsing/clicking
```

Shell/bash nen bi hy sinh o core production.
Neu can chay script, dong goi thanh tool hep:

```text
run_allowed_script(scriptId, args)
validate_csv(fileId)
convert_pdf_to_text(fileId)
run_report_job(reportType, params)
```

Nhung tool nay phai chay trong sandbox/job worker:

- timeout;
- readonly filesystem neu co the;
- no network mac dinh;
- allowlist command;
- full audit;
- idempotency key neu co side effect.

Computer use chi nen la last-resort RPA worker cho legacy app khong co API.
No khong nam trong core Agent Smith.

Neu bat buoc dung computer use:

- chay trong virtual desktop rieng;
- account quyen thap;
- lock theo entity;
- screenshot/audit;
- human approval truoc action nguy hiem;
- transaction nho;
- co rollback/verification neu co the.

## Task Runtime

SAP va enterprise systems co the cham.
Khong nen de agent block lau trong mot tool call.

Nen co task runtime:

```text
start_task(...)
get_task_status(taskId)
read_task_result(taskId)
cancel_task(taskId)
```

Nhung operation cham hoac nguy hiem nen thanh background task:

- SAP reconciliation;
- report generation;
- document extraction;
- batch validation;
- long API workflow;
- RPA job.
