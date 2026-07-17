from __future__ import annotations

import base64
import uuid

import pytest
from pydantic import ValidationError

from agent_smith.app.ports.files import FileRecord
from agent_smith.app.services.attachments import AttachmentError, AttachmentService
from agent_smith.core.agent.harness.session.types import SessionTreeEntry
from agent_smith.core.agent.persistence import (
    FileReferenceContent,
    PersistedUserMessage,
    RUNTIME_IMAGE_MARKER,
    project_message_for_persistence,
)
from agent_smith.core.llm.types import ImageContent, Model, TextContent, UserMessage
from helpers.files import FakeBlobStore, FakeFileCatalog


def _model(*, images: bool = True) -> Model:
    return Model(
        id="model",
        name="model",
        api="litellm",
        provider="test",
        input=["text", "image"] if images else ["text"],
    )


def _ready(
    catalog: FakeFileCatalog,
    blobs: FakeBlobStore,
    *,
    principal_id: str,
    mime_type: str,
    data: bytes,
    name: str = "image",
) -> FileRecord:
    file_id = str(uuid.uuid4())
    record = FileRecord(
        id=file_id,
        principal_id=principal_id,
        original_name=name,
        mime_type=mime_type,
        size_bytes=len(data),
        object_key=f"objects/{file_id}",
        status="ready",
    )
    catalog.records[file_id] = record
    blobs.objects[record.object_key] = (data, mime_type, None)
    return record


@pytest.mark.asyncio
@pytest.mark.parametrize("mime_type", ["image/png", "image/jpeg", "image/gif", "image/webp"])
async def test_materializer_preserves_raw_bytes_and_mime_type(mime_type: str) -> None:
    catalog, blobs = FakeFileCatalog(), FakeBlobStore()
    principal_id = str(uuid.uuid4())
    data = f"raw-{mime_type}".encode()
    record = _ready(
        catalog,
        blobs,
        principal_id=principal_id,
        mime_type=mime_type,
        data=data,
        name="photo",
    )
    service = AttachmentService(catalog, blobs)
    current = await service.resolve_current(
        principal_id=principal_id,
        raw_attachments=[{"fileId": record.id}],
        model=_model(),
    )
    message = PersistedUserMessage(
        content=[TextContent(text="inspect"), *current.references],
        timestamp=1,
    )

    materialized = await service.materialize(
        [message], principal_id=principal_id, model=_model(), current=current
    )

    image = materialized[0].content[1]
    assert isinstance(image, ImageContent)
    assert image.mime_type == mime_type
    assert base64.b64decode(image.data) == data


@pytest.mark.asyncio
async def test_attachment_validation_maps_duplicate_state_type_owner_model_and_budget() -> None:
    catalog, blobs = FakeFileCatalog(), FakeBlobStore()
    principal_id = str(uuid.uuid4())
    ready = _ready(
        catalog, blobs, principal_id=principal_id, mime_type="image/png", data=b"1234"
    )
    service = AttachmentService(catalog, blobs, max_materialized_bytes=3)

    cases = [
        ([{"fileId": ready.id}, {"fileId": ready.id}], 400),
        ([{"bad": ready.id}], 400),
        (None, 400),
        ([{"fileId": str(uuid.uuid4())}], 404),
    ]
    for attachments, status in cases:
        with pytest.raises(AttachmentError) as exc:
            await service.resolve_current(
                principal_id=principal_id,
                raw_attachments=attachments,
                model=_model(),
            )
        assert exc.value.status == status

    with pytest.raises(AttachmentError) as exc:
        await service.resolve_current(
            principal_id=str(uuid.uuid4()),
            raw_attachments=[{"fileId": ready.id}],
            model=_model(),
        )
    assert exc.value.status == 404

    with pytest.raises(AttachmentError) as exc:
        await AttachmentService(catalog, blobs, max_attachments=1).resolve_current(
            principal_id=principal_id,
            raw_attachments=[{"fileId": ready.id}, {"fileId": str(uuid.uuid4())}],
            model=_model(),
        )
    assert exc.value.status == 400
    assert exc.value.code == "too_many_attachments"

    with pytest.raises(AttachmentError) as exc:
        await service.resolve_current(
            principal_id=principal_id,
            raw_attachments=[{"fileId": ready.id}],
            model=_model(images=False),
        )
    assert exc.value.status == 400

    with pytest.raises(AttachmentError) as exc:
        await service.resolve_current(
            principal_id=principal_id,
            raw_attachments=[{"fileId": ready.id}],
            model=_model(),
        )
    assert exc.value.status == 413

    unready = _ready(
        catalog, blobs, principal_id=principal_id, mime_type="image/png", data=b"1"
    )
    catalog.records[unready.id] = FileRecord(**{**unready.__dict__, "status": "uploaded"})
    with pytest.raises(AttachmentError) as exc:
        await AttachmentService(catalog, blobs).resolve_current(
            principal_id=principal_id,
            raw_attachments=[{"fileId": unready.id}],
            model=_model(),
        )
    assert exc.value.status == 409

    document = _ready(
        catalog, blobs, principal_id=principal_id, mime_type="application/pdf", data=b"1"
    )
    with pytest.raises(AttachmentError) as exc:
        await AttachmentService(catalog, blobs).resolve_current(
            principal_id=principal_id,
            raw_attachments=[{"fileId": document.id}],
            model=_model(),
        )
    assert exc.value.status == 415


@pytest.mark.asyncio
async def test_history_budget_is_newest_first_and_tombstones_are_provider_only() -> None:
    catalog, blobs = FakeFileCatalog(), FakeBlobStore()
    principal_id = str(uuid.uuid4())
    old = _ready(
        catalog, blobs, principal_id=principal_id, mime_type="image/png", data=b"oldold", name="old"
    )
    new = _ready(
        catalog, blobs, principal_id=principal_id, mime_type="image/png", data=b"newnew", name="new"
    )
    old_reference = FileReferenceContent(
        fileId=old.id, mimeType=old.mime_type, displayName=old.original_name
    )
    new_reference = FileReferenceContent(
        fileId=new.id, mimeType=new.mime_type, displayName=new.original_name
    )
    messages = [
        PersistedUserMessage(content=[old_reference], timestamp=1),
        PersistedUserMessage(content=[new_reference], timestamp=2),
    ]
    service = AttachmentService(catalog, blobs, max_materialized_bytes=6)

    output = await service.materialize(messages, principal_id=principal_id, model=_model())

    assert isinstance(output[0].content[0], TextContent)
    assert "image budget exceeded" in output[0].content[0].text
    assert isinstance(output[1].content[0], ImageContent)
    # The immutable session objects remain references after provider conversion.
    assert messages[0].content[0] == old_reference
    assert messages[1].content[0] == new_reference


def test_session_projection_rejects_inline_base64_and_marks_unmanaged_runtime_images() -> None:
    runtime = UserMessage(
        content=[TextContent(text="look"), ImageContent(data="aW1hZ2U=", mimeType="image/png")],
        timestamp=1,
    )
    persisted = project_message_for_persistence(runtime)
    assert persisted.model_dump(mode="json", by_alias=True)["content"][1] == {
        "type": "text",
        "text": RUNTIME_IMAGE_MARKER.format(mime_type="image/png"),
        "textSignature": None,
    }
    with pytest.raises(ValidationError):
        SessionTreeEntry.model_validate(
            {
                "id": "entry",
                "type": "message",
                "timestamp": "now",
                "message": runtime.model_dump(mode="json", by_alias=True),
            }
        )
