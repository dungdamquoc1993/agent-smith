"""Bounded, idempotent file-maintenance use cases."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from agent_smith.app.ports.files import BlobStorageError, BlobStore, FileMaintenanceStore


class FileMaintenanceService:
    def __init__(
        self,
        store: FileMaintenanceStore,
        blobs: BlobStore,
        *,
        pending_ttl_seconds: int,
        deleted_retention_seconds: int,
        audit_retention_seconds: int,
    ) -> None:
        self._store = store
        self._blobs = blobs
        self._pending_ttl_seconds = pending_ttl_seconds
        self._deleted_retention_seconds = deleted_retention_seconds
        self._audit_retention_seconds = audit_retention_seconds

    async def cleanup_stale_uploads(self, *, limit: int = 100) -> int:
        cutoff = datetime.now(UTC) - timedelta(seconds=self._pending_ttl_seconds)
        rows = await self._store.list_stale_pending(created_before=cutoff, limit=limit)
        handled = 0
        for row in rows:
            failed = await self._store.mark_expired_upload(
                file_id=row.id,
                principal_id=row.principal_id,
            )
            if failed is None:
                continue
            try:
                await self._blobs.delete(object_key=row.object_key)
            except BlobStorageError:
                continue
            if await self._store.mark_object_deleted(
                file_id=row.id,
                deleted_at=datetime.now(UTC),
            ):
                handled += 1
        return handled

    async def cleanup_rejected_uploads(self, *, limit: int = 100) -> int:
        rows = await self._store.list_rejected_objects(limit=limit)
        handled = 0
        for row in rows:
            try:
                await self._blobs.delete(object_key=row.object_key)
            except BlobStorageError:
                continue
            if await self._store.mark_object_deleted(
                file_id=row.id,
                deleted_at=datetime.now(UTC),
            ):
                handled += 1
        return handled

    async def cleanup_deleted_files(self, *, limit: int = 100) -> int:
        cutoff = datetime.now(UTC) - timedelta(seconds=self._deleted_retention_seconds)
        rows = await self._store.list_deleted(deleted_before=cutoff, limit=limit)
        handled = 0
        for row in rows:
            if row.object_deleted_at is None:
                try:
                    prefix = row.object_key.rsplit("/original", 1)[0] + "/"
                    await self._blobs.delete_prefix(prefix=prefix)
                except BlobStorageError:
                    continue
                marked = await self._store.mark_object_deleted(
                    file_id=row.id,
                    deleted_at=datetime.now(UTC),
                )
                if marked is None:
                    continue
            purged = await self._store.purge_file(file_id=row.id)
            if row.object_deleted_at is None or purged:
                handled += 1
        return handled

    async def cleanup_audit_events(self, *, limit: int = 100) -> int:
        cutoff = datetime.now(UTC) - timedelta(seconds=self._audit_retention_seconds)
        return await self._store.purge_audit_events_before(
            occurred_before=cutoff,
            limit=limit,
        )
