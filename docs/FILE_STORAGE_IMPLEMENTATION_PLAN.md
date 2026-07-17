# Managed File Storage Implementation Plan

## Mục tiêu

Xây dựng file storage do Agent Smith quản lý hoàn toàn:

- Postgres lưu metadata, ownership và lifecycle.
- S3-compatible object storage (AWS S3 hoặc Cloudflare R2) lưu binary.
- Client upload trực tiếp bằng presigned URL, không nhận S3 credentials.
- Session chỉ lưu file reference, không lưu binary/base64.
- App layer chuyển file thành nội dung LLM hiểu được trước khi gọi Harness/provider.

## Quyết định kiến trúc đã thống nhất

- [x] Chỉ hỗ trợ một ownership mode: Agent Smith managed storage.
- [x] Không hỗ trợ partner-owned external file URL/reference.
- [x] Không lưu binary trong Postgres hoặc `SessionEntry.payload`.
- [x] Postgres và S3 không dùng distributed transaction; consistency được quản lý bằng state machine và cleanup/retry.
- [x] Bucket luôn private; download cũng qua authorization và presigned URL.
- [x] Harness không biết S3, R2, DOCX, PDF hoặc file lifecycle.
- [x] Cloudflare R2 dùng chung S3-compatible adapter, khác configuration.

## Contract MVP đã triển khai

- MIME allowlist: PNG, JPEG, GIF, WebP, plain text, Markdown, CSV, PDF,
  DOC/DOCX và XLS/XLSX.
- Giới hạn mặc định: 50 MiB/file; có thể cấu hình bằng environment.
- Presigned upload/download URL: 15 phút.
- Pending upload hết hạn sau 1 giờ; soft-deleted metadata giữ 7 ngày.
- Filename được phép trùng; UUID mới là định danh thật và object key không chứa filename.
- Pagination mặc định 50, tối đa 100.
- P1/P2 chỉ lưu trữ và download. Processing, session attachment và LLM input bắt đầu từ P3.
- Cross-principal access trả `404 file_not_found`, không dùng `403`, để không tiết lộ
  một file ID có tồn tại hay không.

Flow browser upload:

```text
Browser -> Partner backend (authenticate user)
Partner backend -> Agent Smith (provider API key + one-time app assertion)
Agent Smith -> Browser (short-lived presigned PUT URL, no S3 credentials)
Browser -> S3/R2 (PUT binary directly)
Partner backend -> Agent Smith (complete upload with a new assertion)
```

## Lifecycle chuẩn

```text
pending_upload
      |
      v
   uploaded
      |
      v
  processing ------> failed
      |
      v
    ready
      |
      v
   deleted
```

Quy tắc:

- File chỉ được dùng bởi session/LLM khi ở trạng thái `ready`.
- `complete upload` phải idempotent.
- `deleted` là soft-delete ở Postgres; xóa object vật lý là async cleanup.
- Upload hết hạn hoặc object không có metadata hợp lệ phải được cleanup.

---

## Milestone 0 — Contract, authentication và giới hạn MVP

### Product/API decisions

- [x] Chốt danh sách MIME type được phép upload trong MVP.
- [x] Chốt dung lượng tối đa cho một file.
- [x] Chốt thời hạn presigned upload/download URL.
- [x] Chốt retention cho file đã soft-delete.
- [x] Chốt hành vi khi filename trùng nhau (cho phép trùng, ID là định danh thật).
- [x] Chốt file nào được xử lý đồng bộ và file nào cần worker.
- [x] Chốt API pagination mặc định và giới hạn tối đa.

### Principal authentication

- [x] Tách dependency/service resolve `VerifiedActor` và `principal_id` dùng chung cho invocation và file routes.
- [x] Không dùng default/test principal cho production file ownership.
- [x] File routes dùng cùng provider API key + trusted app assertion với partner invocation.
- [x] Đảm bảo assertion replay protection vẫn hoạt động khi gọi file endpoints.
- [x] Viết rõ flow Browser → Partner backend → Agent Smith → presigned URL → S3/R2.
- [x] Thêm authorization tests cho cross-principal access.

### Definition of done

- [x] Có API contract được chốt trước khi tạo migration.
- [x] Mọi user-facing file operation đều có một authenticated `principal_id`.
- [x] MVP scope không bao gồm folders, OCR, vector indexing hoặc multipart upload.

---

## Milestone 1 — File metadata và storage foundation

### Postgres schema

- [x] Tạo migration `010_managed_files.py`.
- [x] Tạo Postgres enum `file_status`:
  - [x] `pending_upload`
  - [x] `uploaded`
  - [x] `processing`
  - [x] `ready`
  - [x] `failed`
  - [x] `deleted`
- [x] Tạo bảng `files` với các cột:
  - [x] `id UUID PRIMARY KEY`
  - [x] `principal_id UUID NOT NULL REFERENCES principals(id)`
  - [x] `original_name VARCHAR(512) NOT NULL`
  - [x] `mime_type VARCHAR(255) NOT NULL`
  - [x] `size_bytes BIGINT NOT NULL`
  - [x] `sha256 VARCHAR(64) NULL`
  - [x] `object_key VARCHAR(1024) NOT NULL UNIQUE`
  - [x] `status file_status NOT NULL`
  - [x] `etag VARCHAR(255) NULL`
  - [x] `failure_reason TEXT NULL`
  - [x] `metadata JSONB NOT NULL DEFAULT '{}'`
  - [x] `created_at`, `updated_at`, `deleted_at`
- [x] Tạo index `(principal_id, status, created_at)`.
- [x] Tạo index `(principal_id, original_name)` cho library MVP.
- [x] Không đặt unique constraint trên `sha256`.
- [x] Viết downgrade migration.

### SQLAlchemy model

- [x] Tạo `infra/storage/postgres/models/file.py`.
- [x] Export model qua `infra/storage/postgres/models/__init__.py`.
- [x] Đăng ký model với Alembic environment.
- [x] Không thêm relationship với `Principal` vì chưa cần query/navigation.

### App contracts

- [x] Tạo `FileRecord` và các request/result types độc lập với SQLAlchemy/S3 SDK.
- [x] Tạo `FileCatalog` port:
  - [x] `create_pending()`
  - [x] `get_file()`
  - [x] `list_files()`
  - [x] `mark_uploaded()`
  - [x] `mark_processing()`
  - [x] `mark_ready()`
  - [x] `mark_failed()`
  - [x] `soft_delete()`
- [x] Tạo `BlobStore` port:
  - [x] `create_upload_url()`
  - [x] `create_download_url()`
  - [x] `stat()`
  - [x] `read_range()`
  - [x] `delete()`
- [x] Định nghĩa app-level errors; ownership mismatch cố ý map thành not found thay vì forbidden.

### Postgres adapter

- [x] Tạo `infra/storage/postgres/adapters/files.py`.
- [x] Enforce mọi user query bằng `principal_id` tại repository boundary.
- [x] Implement optimistic/idempotent state transitions.
- [x] Không trả SQLAlchemy model ra App layer.
- [x] Thêm adapter tests với Postgres khi test URL được cấu hình.

### S3-compatible adapter

- [x] Thêm boto3; mọi blocking SDK call được bọc bằng `asyncio.to_thread()`.
- [x] Tạo `infra/storage/s3/client.py`.
- [x] Tạo `infra/storage/s3/blob_store.py`.
- [x] Implement presigned `PUT` upload URL.
- [x] Implement presigned `GET` download URL.
- [x] Implement `HEAD/stat` object.
- [x] Implement range read để MIME sniffing.
- [x] Implement object delete.
- [x] Object key dùng UUID, không dùng filename trực tiếp.
- [x] Không log secret key hoặc full presigned URL.
- [x] Chuẩn hóa S3/R2 errors thành app-level `BlobStorageError`.

### Configuration và composition root

- [x] Thêm settings:
  - [x] `AGENT_SMITH_S3_ENDPOINT_URL`
  - [x] `AGENT_SMITH_S3_REGION`
  - [x] `AGENT_SMITH_S3_BUCKET`
  - [x] `AGENT_SMITH_S3_ACCESS_KEY_ID`
  - [x] `AGENT_SMITH_S3_SECRET_ACCESS_KEY`
  - [x] `AGENT_SMITH_S3_PRESIGN_TTL_SECONDS`
  - [x] `AGENT_SMITH_FILE_MAX_BYTES`
- [x] Validate empty/invalid configuration khi bootstrap settings/file service.
- [x] Wire `PostgresFileCatalog` và `S3BlobStore` trong `AppContainer`.
- [x] Thêm bucket CORS documentation cho browser direct upload.
- [x] Cập nhật `.env.example` và Docker docs.

### Tests

- [x] Tạo `FakeBlobStore` dưới `tests/helpers`, không đưa memory implementation vào production.
- [x] Unit test object-key generation.
- [x] Unit test presign request parameters.
- [x] Unit test S3 error mapping.
- [x] Thêm contract test opt-in với MinIO hoặc S3-compatible endpoint.
- [x] Architecture test đảm bảo S3 SDK chỉ nằm trong `infra/storage/s3`.

### Definition of done

- [x] App layer chỉ biết `FileCatalog` và `BlobStore`.
- [x] Có thể tạo pending metadata và tạo presigned URL qua service-level test.
- [x] Migration upgrade/downgrade SQL được Alembic validate.
- [x] Không có binary/base64 đi vào Postgres.

---

## Milestone 2 — Upload lifecycle và library HTTP API

### FileService

- [x] Tạo `app/services/files.py`.
- [x] Implement `initiate_upload()`:
  - [x] Validate filename.
  - [x] Validate declared MIME type.
  - [x] Validate declared size/quota.
  - [x] Sinh `file_id` và opaque `object_key`.
  - [x] Tạo `pending_upload` record.
  - [x] Trả presigned URL, method, required headers và expiry.
- [x] Implement `complete_upload()`:
  - [x] Idempotent khi đã `uploaded`, `processing` hoặc `ready`.
  - [x] Reject file ở state không hợp lệ.
  - [x] `HEAD` object và kiểm tra object tồn tại.
  - [x] So sánh actual size với declared size.
  - [x] So sánh checksum khi storage/client hỗ trợ.
  - [x] MIME sniff từ bytes đầu, không chỉ tin `Content-Type`.
  - [x] Mark `uploaded` hoặc `failed`.
- [x] Implement `list_files()` với cursor pagination/filter status/MIME.
- [x] Implement `get_file()`.
- [x] Implement `create_download_url()` với ownership check.
- [x] Implement `delete_file()` bằng soft-delete; worker boundary expose cleanup idempotent.
- [x] Không cho download file `pending_upload`, `failed` hoặc `deleted`.

### HTTP routes

- [x] Tạo `transports/http/file_routes.py`.
- [x] Đăng ký router trong `transports/http/main.py`.
- [x] Implement endpoints:
  - [x] `POST /api/files/uploads`
  - [x] `POST /api/files/{fileId}/complete`
  - [x] `GET /api/files`
  - [x] `GET /api/files/{fileId}`
  - [x] `POST /api/files/{fileId}/download-url`
  - [x] `DELETE /api/files/{fileId}`
- [x] Chuẩn hóa response casing theo API hiện tại.
- [x] Chuẩn hóa error codes/status:
  - [x] `400 invalid_file`
  - [x] `401 unauthorized`
  - [x] Ownership mismatch không trả `403 file_forbidden`; cố ý trả `404`.
  - [x] `404 file_not_found`
  - [x] `409 invalid_file_state`
  - [x] `413 file_too_large`
  - [x] `502 storage_unavailable`
- [x] Không tạo multipart proxy upload endpoint trong MVP.

### Cleanup và consistency

- [x] Cleanup `pending_upload` records hết hạn.
- [ ] Cleanup orphan objects không có valid metadata record.
- [x] Retry object delete khi S3/R2 tạm lỗi ở lần cleanup tiếp theo.
- [x] DB create thành công nhưng presign thất bại: mark metadata `failed`.
- [x] Object upload thành công nhưng client không gọi complete: stale-upload cleanup xóa object và mark `failed`.
- [x] Các cleanup operations phải idempotent.

> Orphan bucket-wide reconciliation chưa bật trong P2: quét/xóa toàn bucket mà
> không có inventory cursor và safety window đủ chặt dễ xóa nhầm object đang upload.
> Pending/deleted objects đã có deterministic cleanup; reconciliation toàn bucket
> được giữ lại cho milestone operations với S3 Inventory hoặc paginated scanner.

### Tests

- [x] Service test: happy-path initiate → upload fake → complete.
- [x] Service test: complete gọi hai lần.
- [x] Service test: object không tồn tại.
- [x] Service test: size mismatch.
- [x] Service test: MIME mismatch.
- [x] Service test: expired pending upload.
- [x] Route test: missing/invalid auth.
- [x] Route test: principal A không đọc/xóa file của principal B.
- [x] Route test: list pagination/filter.
- [x] Route test: deleted file không tạo download URL.

### Definition of done

- [x] Browser có thể upload trực tiếp lên S3/R2 mà không có storage credentials.
- [x] User có thể list/get/download/delete file thuộc mình.
- [x] API không nhận hoặc relay binary.
- [x] File library backend hoạt động nhưng chưa đưa file vào session/LLM.

---

## Milestone 3 — Session attachments và image input

### Quyết định kiến trúc đã chốt

- [x] Persisted session content dùng generic `FileReferenceContent` gồm
  `fileId`, `mimeType` và `displayName`; không persist binary/base64 mới.
- [x] `FileReferenceContent` thuộc session contract trong Core nhưng chỉ là một
  immutable reference. Core/Harness không import hoặc gọi `FileService`,
  `BlobStore`, S3 hay R2.
- [x] Persisted message và provider-ready message là hai representation riêng.
  App inject async resolver/`convert_to_llm` để materialize reference thành
  `ImageContent` ngay trước provider request.
- [x] Public invocation contract dùng `payload.attachments`, không dùng mảng
  `fileIds` thuần:

  ```json
  {
    "payload": {
      "prompt": "Phân tích ảnh này",
      "attachments": [
        {"fileId": "..."}
      ]
    }
  }
  ```

- [x] `purpose` của binding do server quản lý; M3 dùng giá trị `input`.
- [x] `session_entry_files.session_entry_id` dùng `ON DELETE CASCADE`;
  `file_id` dùng `ON DELETE RESTRICT`.
- [x] Xóa file đã attach sẽ ẩn file khỏi library, cấm download/reuse và xóa
  object sau retention. Session giữ tombstone/reference để bảo toàn lịch sử và
  audit; metadata chưa được hard-delete khi còn session reference.
- [x] Fork session clone attachment bindings sang entry mới nhưng dùng lại cùng
  `file_id`; không copy object hoặc binary.
- [x] Image hợp lệ đi thẳng từ `uploaded` sang `ready`; document tiếp tục ở
  `uploaded` cho đến Milestone 4.
- [x] M3 chỉ materialize PNG, JPEG, GIF và WebP; model phải khai báo hỗ trợ
  `image`.
- [x] Persisted inline base64 cũ không được hỗ trợ và bị strict session reader
  reject; không chạy migration đổi dữ liệu cũ vì không có production data cần giữ.
- [x] Active session branch materialize các image references còn trong context
  để hỗ trợ hỏi tiếp về ảnh cũ. Recent conversations từ session khác không tự
  động materialize attachment.
- [x] Giới hạn mặc định là 8 attachments mỗi invocation và tổng 20 MiB image
  bytes được materialize; cả hai đều configurable.
- [x] Không dùng remote image URL, provider upload API, provider asset ID hoặc
  provider-side cache. Provider asset IDs không thuộc kiến trúc M3 hiện tại.
- [x] `/api/prompt/stream` nhận `attachments` ở root và
  `/api/agent/invoke/stream` nhận `payload.attachments`; prompt có thể rỗng chỉ
  khi có ít nhất một attachment hợp lệ.

### Database

- [x] Tạo migration `011_session_entry_files.py`, đồng thời thêm
  `files.object_deleted_at` để cleanup object idempotent.
- [x] Tạo bảng `session_entry_files`:
  - [x] `session_entry_id UUID REFERENCES session_entries(id)`
  - [x] `file_id UUID REFERENCES files(id)`
  - [x] `position INTEGER NOT NULL`
  - [x] `purpose VARCHAR(32) NOT NULL`
- [x] Dùng primary key `(session_entry_id, position)` và unique constraint
  `(session_entry_id, file_id, purpose)` để giữ thứ tự và chặn duplicate binding.
- [x] Thêm index theo `session_entry_id` và `file_id`.
- [x] Chốt delete behavior để session history không mất audit reference.

### Persisted message contract

- [x] Định nghĩa persisted `FileReferenceContent` chứa `fileId`, MIME và display name.
- [x] Không dùng `ImageContent.data` base64 làm persisted representation.
- [x] Tách persisted session content khỏi provider-ready content.
- [x] Không giữ backward compatibility cho persisted inline image cũ.
- [x] Runtime-only image từ Harness/MCP/tool được project thành marker khi persist,
  nhưng giữ overlay trong run hiện tại qua các tool turn.
- [x] Append session entry và attachment bindings trong cùng Postgres transaction;
  khóa/revalidate file row trước khi bind.

### Invocation và App resolution

- [x] Bổ sung `attachments: [{fileId}]` vào cả hai streaming contract.
- [x] Validate mọi file thuộc cùng `principal_id` với invocation.
- [x] Reject file chưa `ready`.
- [x] Reject MIME/model input không được hỗ trợ.
- [x] Preserve attachment ordering.
- [x] Materialize nguyên bytes PNG/JPEG/GIF/WebP ngay trước provider request.
- [x] Full-object read có byte bound và S3 reads có concurrency limit.
- [x] Không giữ S3 connection hoặc bytes lâu hơn một provider turn.
- [x] Giới hạn current attachments 8 item/20 MiB; history được chọn newest-first
  và phần không materialize trở thành provider-only tombstone.

### Session/Harness/provider boundary

- [x] Session contract có thể giữ generic file reference; provider request chỉ
  nhận resolved text/image content.
- [x] S3/file services không được import vào Core/Harness.
- [x] Dùng async `convert_to_llm` để resolve references.
- [x] Compaction serialize reference thành marker, không gửi binary ảnh cũ vào
  compaction request; session replay vẫn giữ reference.
- [x] Active branch materialize reference còn trong context; recent conversation
  context không tự động load attachment từ session khác.

### Tests

- [x] Session entry persist file reference, không chứa base64.
- [x] Entry + attachment binding rollback cùng nhau khi lỗi.
- [x] Image materialization tạo đúng provider payload cho bốn MIME type.
- [x] Cross-principal attachment bị reject.
- [x] Deleted/unready file bị reject.
- [x] Model không hỗ trợ image trả lỗi rõ ràng.
- [x] Fork session clone attachment bindings đúng cách.

### Definition of done

- [x] User có thể attach ảnh đã upload vào prompt.
- [x] Session replay materialize lại reference từ managed storage sau restart.
- [x] Postgres không chứa image binary/base64 mới.

---

## Milestone 4 — Document processing

### Processing contracts

- [ ] Tạo `FileProcessor` port.
- [ ] Định nghĩa `ProcessingResult` gồm extracted text, tables, page images và metadata.
- [ ] Processor selection dựa trên MIME sniffed type, không dựa riêng vào extension.
- [ ] Processor errors được phân loại retryable/non-retryable.

### Processing schema

- [ ] Tạo `file_derivatives` table:
  - [ ] `id`, `file_id`, `kind`
  - [ ] `object_key`, `mime_type`, `size_bytes`
  - [ ] `metadata`, timestamps
- [ ] Cân nhắc/tạo `file_processing_jobs` table:
  - [ ] status, attempts, processor
  - [ ] error, started/completed timestamps
- [ ] Derivatives cũng lưu trên S3/R2, không lưu binary trong Postgres.

### Worker orchestration

- [ ] Chọn durable job mechanism thay cho memory task runtime.
- [ ] Enqueue processing sau khi complete upload.
- [ ] Implement retry/backoff/idempotency.
- [ ] Không giữ Postgres transaction trong lúc download/process/upload derivative.
- [ ] Mark `ready` chỉ sau khi required processing hoàn tất.
- [ ] Emit observable status/progress cho UI.

### Format support matrix

- [ ] Plain text (`text/plain`).
- [ ] Markdown (`text/markdown`, `.md`).
- [ ] CSV (`text/csv`).
- [ ] PDF có text layer.
- [ ] DOCX.
- [ ] XLSX.
- [ ] Image metadata/validation.
- [ ] Legacy DOC — chốt converter/sandbox strategy trước khi implement.
- [ ] Legacy XLS — chốt converter strategy trước khi implement.
- [ ] Scanned PDF/OCR — để ngoài initial document-processing MVP nếu cần.

### LLM materialization

- [ ] Tài liệu ngắn có thể materialize extracted text trực tiếp.
- [ ] Tài liệu dài có chunking/token budget.
- [ ] CSV/XLSX giữ sheet/table boundaries trong normalized representation.
- [ ] PDF giữ page provenance.
- [ ] Không tự động đưa toàn bộ library vào context.

### Tests

- [ ] Golden fixtures cho từng format hỗ trợ.
- [ ] Corrupt file fixtures.
- [ ] Password-protected document behavior.
- [ ] Oversized/zip-bomb protection.
- [ ] Retry và idempotency tests.
- [ ] Extracted content giữ provenance về file/page/sheet.

### Definition of done

- [ ] Upload tài liệu chuyển đúng `uploaded → processing → ready/failed`.
- [ ] Prompt có thể sử dụng extracted content mà không cần partner preprocess.
- [ ] Processing failure không làm hỏng original file/library metadata.

---

## Milestone 5 — Library UX: folders, rename, move và search

### Folders

- [ ] Chốt một file chỉ thuộc một folder hay có thể thuộc nhiều folder.
- [ ] Tạo `folders` table với `principal_id`, `parent_id`, `name`.
- [ ] Enforce unique folder name trong cùng parent/principal nếu product yêu cầu.
- [ ] Detect/prevent folder cycles.
- [ ] Folder chỉ là metadata; không map thành S3 prefix hierarchy.

### Library API

- [ ] Create/rename/move/delete folder endpoints.
- [ ] Rename file display name.
- [ ] Move file giữa folders.
- [ ] Filter theo image/document/spreadsheet/MIME.
- [ ] Search filename.
- [ ] Sort theo name/updated_at/size.
- [ ] Cursor pagination cho library lớn.
- [ ] Bulk delete/move nếu UI cần.

### Tests

- [ ] Cross-principal folder/file isolation.
- [ ] Folder cycle prevention.
- [ ] Delete non-empty folder behavior.
- [ ] Pagination ổn định khi có concurrent insert/delete.

### Definition of done

- [ ] Backend hỗ trợ đầy đủ UI library cơ bản như mockup.
- [ ] Library organization không làm thay đổi object key hoặc copy binary.

---

## Milestone 6 — Security, reliability và operations hardening

### Security

- [ ] Private bucket policy được document và kiểm thử.
- [ ] Presigned URL TTL ngắn và scope đúng object/method.
- [ ] MIME allowlist + sniffing.
- [ ] Filename sanitization cho display/header, không dùng cho object key.
- [ ] Malware scanning/quarantine trước `ready`.
- [ ] Zip-bomb/decompression limits.
- [ ] Rate limit upload initiation/completion.
- [ ] Quota theo principal/tenant.
- [ ] Audit log cho create/download/delete/attach.
- [ ] Không log credentials, raw assertion hoặc presigned URL.

### Reliability

- [ ] Metrics theo status và processing latency.
- [ ] S3 error rate/latency metrics.
- [ ] Pending/orphan cleanup metrics.
- [ ] Dead-letter/retry visibility cho processing jobs.
- [ ] Backup/restore strategy cho metadata.
- [ ] Lifecycle/retention policy cho object storage.
- [ ] Reconciliation job giữa Postgres metadata và object storage.

### Scale features

- [ ] Multipart/resumable upload cho file lớn.
- [ ] Cancel multipart upload và cleanup incomplete parts.
- [ ] CDN strategy cho download nếu cần.
- [ ] Thumbnail generation.
- [ ] Deduplication chỉ sau khi có threat/privacy analysis.

### Definition of done

- [ ] Có runbook cho storage outage, stuck processing và orphan objects.
- [ ] Có dashboard/alerts cho các failure mode chính.
- [ ] Security review hoàn tất trước production rollout rộng.

---

## Future integrations — ngoài initial implementation

- [ ] Qdrant vector indexing cho extracted chunks.
- [ ] Elasticsearch full-text search cho library content.
- [ ] Generated files/tool outputs lưu lại vào cùng file catalog.
- [ ] Sharing/ACL ngoài principal ownership.
- [ ] File versioning.
- [ ] Cross-session knowledge collections.

---

## Test matrix tổng thể

- [ ] Unit tests không cần Postgres/S3 thật.
- [ ] Postgres integration tests chạy khi có `AGENT_SMITH_TEST_POSTGRES_URL`.
- [ ] S3 contract tests chạy với MinIO hoặc dedicated test bucket.
- [ ] HTTP tests dùng fake ports và kiểm tra auth/error mapping.
- [ ] End-to-end test: initiate → direct upload → complete → list → download → delete.
- [ ] End-to-end test: upload image → attach prompt → session replay.
- [ ] End-to-end test: upload document → process → prompt sử dụng extracted content.
- [ ] Architecture tests giữ SQLAlchemy trong Postgres backend và S3 SDK trong S3 backend.

## Thứ tự triển khai đề xuất

- [ ] Delivery 1: Milestone 0 + Milestone 1.
- [ ] Delivery 2: Milestone 2 — managed upload/library API hoàn chỉnh.
- [ ] Delivery 3: Milestone 3 — session attachments và image input.
- [ ] Delivery 4: Milestone 4 — document processing.
- [ ] Delivery 5: Milestone 5 + phần production-required của Milestone 6.

Không bắt đầu milestone sau trước khi definition of done của milestone trước đã được kiểm chứng,
trừ các research spike không thay đổi production contract.
