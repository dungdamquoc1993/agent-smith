# Document Processing

Milestone 4 có hai lớp độc lập, nối với nhau bằng derivative đã persist:

```text
upload complete
  -> Postgres processing job
  -> worker: sniff -> processor -> NormalizedDocument
  -> S3/R2 derivatives + Postgres pointers
  -> file ready

prompt attachment reference
  -> resolver reads ready derivatives
  -> whole text or budgeted lexical chunks
  -> provider TextContent / ImageContent only
```

`NormalizedDocument` không được chuyển in-memory thẳng từ worker sang resolver.
Worker và HTTP process có thể restart hoặc scale độc lập; resolver luôn đọc
`normalized_document`, `extracted_text`, và `chunks` từ private object storage.
Postgres chỉ giữ lifecycle, job, progress, processor version, error, metadata và
object key.

## Processor boundary

Mỗi format có một processor riêng sau content-first MIME detection. Registry
map sniffed MIME sang processor; extension chỉ được dùng như text/Markdown hint,
không quyết định processor cho binary document. Processor trả cùng một schema:

- ordered blocks: heading, paragraph hoặc table;
- provenance: page, sheet, cell range, section và row range;
- metadata/warnings;
- optional page-image artifacts cho strategy tương lai.

MVP hỗ trợ TXT, Markdown, CSV, PDF có text layer, DOCX và XLSX. PNG/JPEG/GIF/WebP
đi fast path nhưng được decode/validate và lưu dimensions/format metadata.
Legacy DOC/XLS bị từ chối trước presign; converter tương lai phải là process hoặc
service sandbox riêng. Scanned PDF/OCR, vision extraction, embeddings, vector
search và graph retrieval không nằm trong MVP này.

## Durable jobs

`file_processing_jobs` trong Postgres là queue bền vững. Worker claim bằng row
lock/`SKIP LOCKED`, đặt lease, heartbeat trong lúc xử lý và có thể reclaim lease
hết hạn. Retryable failures dùng exponential full-jitter backoff, tối đa 5 lần
mặc định. Non-retryable hoặc exhausted jobs chuyển file sang `failed`.

Không có Postgres transaction nào mở trong lúc download original, parse hoặc
upload derivatives. Transaction cuối upsert derivative pointers rồi chuyển
`processing -> ready`. Artifact key/id phụ thuộc pipeline version và content nên
retry an toàn. Reconciliation định kỳ enqueue các file `uploaded` bị bỏ sót.

Chạy worker riêng:

```bash
poetry run python -m agent_smith.workers.main
```

UI poll `GET /api/files/{fileId}` hoặc `GET /api/files`; field `processing` trả
`status`, `phase`, `progressPercent`, attempts, processor và stable error object.

## Prompt materialization

Resolver chỉ xét file reference có trong conversation/current invocation, không
quét toàn bộ library. Reference mới nhất của cùng file được materialize; reference
cũ thành marker. Tài liệu ngắn dùng toàn bộ `extracted_text`. Khi không đủ context,
resolver rank các chunk bằng lexical BM25-style scoring trong process, ưu tiên
current attachments và giữ page/sheet provenance.

Budget tài liệu là giá trị nhỏ nhất giữa cấu hình 32k token và phần context còn
lại sau output/headroom/conversation. Provider cuối chỉ nhận text và image blocks:
document original URL/binary không được gửi cho provider; ảnh mới được base64 hóa
tạm thời tại provider boundary.

## Failure semantics

Original và library metadata không bị xóa khi processing thất bại. File `failed`
vẫn có thể tạo download URL; chỉ attachment-to-LLM bị chặn. Error được phân loại:

- non-retryable: MIME mismatch, unsupported/legacy type, corrupt or encrypted
  document, scanned PDF without OCR, limits/zip-bomb;
- retryable: temporary storage failure, processor unavailable, timeout, lost
  worker lease và unexpected processor failure.

Delete là soft-delete, cancel pending/running jobs, rồi cleanup original và mọi
derivative dưới file prefix sau retention.
