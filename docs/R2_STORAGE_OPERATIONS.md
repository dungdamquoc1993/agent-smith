# Private Cloudflare R2 Operations

Đây là cấu hình production cho trusted partner pilot. Bucket Agent Smith phải
private: không bật `r2.dev` public access và không gắn public custom domain.
Presigned URLs dùng S3 API domain và được coi như bearer token; không log URL hoặc
query string.

Cloudflare references:

- [Presigned URLs](https://developers.cloudflare.com/r2/api/s3/presigned-urls/)
- [R2 API token permissions](https://developers.cloudflare.com/r2/api/tokens/)
- [Bucket CORS](https://developers.cloudflare.com/r2/buckets/cors/)

## Runtime configuration

```dotenv
AGENT_SMITH_S3_PROVIDER=r2
AGENT_SMITH_S3_ENDPOINT_URL=https://<account-id>.r2.cloudflarestorage.com
AGENT_SMITH_S3_REGION=auto
AGENT_SMITH_S3_BUCKET=<agent-smith-private-bucket>
AGENT_SMITH_S3_ACCESS_KEY_ID=<bucket-scoped-token-access-key>
AGENT_SMITH_S3_SECRET_ACCESS_KEY=<bucket-scoped-token-secret>
AGENT_SMITH_S3_PATH_STYLE=false
AGENT_SMITH_S3_PRESIGN_TTL_SECONDS=600
```

Tạo R2 API token có permission **Object Read & Write** và scope chỉ đúng bucket
Agent Smith. Không dùng Admin token hoặc token áp dụng cho mọi bucket. HTTP process
và document worker dùng cùng bucket/credentials; secrets chỉ nằm trong runtime
secret store, không commit vào `.env`.

R2 không hỗ trợ presigned POST. Presigned PUT không enforce được
`content-length-range`, vì vậy application kiểm tra declared size tối đa 50 MiB
khi initiate và kiểm tra actual object size khi complete. Đây là risk acceptance
chỉ dành cho trusted pilot.

## Browser CORS

Thay origin mẫu bằng exact partner origin; origin không có path hoặc trailing slash.
Chỉ thêm checksum header khi client gửi checksum:

```json
[
  {
    "AllowedOrigins": ["https://partner.example.com"],
    "AllowedMethods": ["PUT", "GET", "HEAD"],
    "AllowedHeaders": ["Content-Type", "x-amz-checksum-sha256"],
    "ExposeHeaders": ["ETag"],
    "MaxAgeSeconds": 3600
  }
]
```

Không dùng wildcard origin. Sau thay đổi, verify browser preflight từ từng partner
origin và chạy R2 contract suite.

## Token rotation

1. Tạo token Object Read & Write mới, scope đúng bucket; giữ token cũ active.
2. Cập nhật cả HTTP process và worker bằng Access Key ID/Secret mới rồi restart.
3. Chạy R2 contract suite và một partner smoke flow upload/download/delete.
4. Xác nhận worker processing và maintenance truy cập R2 bình thường.
5. Revoke token cũ. Không in hoặc paste secret vào log/ticket/chat.

## Contract suite

Chỉ chạy trên dedicated test bucket; mọi object dùng prefix `contract-tests/` và
được xóa trong teardown:

```bash
AGENT_SMITH_TEST_S3_ENDPOINT_URL=https://<account-id>.r2.cloudflarestorage.com \
AGENT_SMITH_TEST_S3_REGION=auto \
AGENT_SMITH_TEST_S3_BUCKET=<dedicated-test-bucket> \
AGENT_SMITH_TEST_S3_ACCESS_KEY_ID=<test-access-key> \
AGENT_SMITH_TEST_S3_SECRET_ACCESS_KEY=<test-secret> \
AGENT_SMITH_TEST_S3_PATH_STYLE=false \
poetry run pytest -q tests/test_s3_contract.py
```

## Troubleshooting

R2 outage:

- HTTP trả `502 storage_unavailable`; không retry request bằng presigned URL đã
  lộ ra log hoặc ticket.
- Xem Cloudflare status và kiểm tra endpoint/bucket/region `auto`; không log
  credentials để debug.
- Processing job retry bằng durable queue. Rejected/deleted object giữ metadata
  chưa có `object_deleted_at` để maintenance retry.

Upload stuck:

- `pending_upload` quá một giờ được worker mark `upload_expired`, xóa object và
  ghi `object_deleted_at`; request mới có thể initiate lại.
- Nếu record không chuyển sau hơn một maintenance interval (mặc định 5 phút),
  kiểm tra worker đang chạy và kết nối chung Postgres/R2 với HTTP process.

Cleanup failure:

- Mỗi lượt worker log một summary chỉ có counts và duration, không có filename,
  object key hoặc URL.
- R2 error không dừng worker; record giữ nguyên để retry ở lượt sau.
- `object_deleted_at` đã có nghĩa là object group đã xử lý; cleanup idempotent
  không xóa hoặc tính lại object đó.

Audit outage:

- Download URL fail closed với `503 audit_unavailable`; không phát hành URL chưa audit.
- Kiểm tra Postgres/migration `013_file_storage_hardening` trước khi retry.
