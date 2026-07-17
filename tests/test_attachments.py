from __future__ import annotations

import base64
import uuid
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from agent_smith.app.invocation import ActorProfile, VerifiedActor
from agent_smith.app.ports.files import FileAuditUnavailable, FileRecord
from agent_smith.app.services.agent_runs import AgentRunService
from agent_smith.app.services.attachments import AttachmentError, AttachmentService
from agent_smith.core.agent.harness.session.types import SessionTreeEntry
from agent_smith.core.agent.persistence import (
    FileReferenceContent,
    PersistedUserMessage,
    RUNTIME_IMAGE_MARKER,
    project_message_for_persistence,
)
from agent_smith.core.llm.types import ImageContent, Model, TextContent, UserMessage
from helpers.files import FakeBlobStore, FakeFileCatalog, FakeFileProcessingStore


class _InvocationAuthentication:
    def __init__(self, principal_id: str, actor: VerifiedActor) -> None:
        self.principal_id = principal_id
        self.actor = actor

    async def authenticate(self, **_values: object):
        return SimpleNamespace(principal_id=self.principal_id, actor=self.actor)


class _InvocationSessions:
    async def open_or_create_session_for_principal(self, **_values: object):
        return SimpleNamespace()


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


def _invocation_service(
    catalog: FakeFileCatalog,
    blobs: FakeBlobStore,
    *,
    principal_id: str,
) -> AgentRunService:
    actor = VerifiedActor(
        issuer="partner",
        subject="partner-user",
        jti=str(uuid.uuid4()),
        providerId=None,
        providerSlug="partner",
        expiresAt=2_000_000_000,
        actor=ActorProfile(displayName="Partner User"),
    )
    service = AgentRunService(
        session_service=_InvocationSessions(),  # type: ignore[arg-type]
        resource_service=SimpleNamespace(default_agent_name="agent"),  # type: ignore[arg-type]
        default_permission_mode="default",
        default_model_key="model",
        authentication_service=_InvocationAuthentication(principal_id, actor),  # type: ignore[arg-type]
        attachment_service=AttachmentService(catalog, blobs),
        file_audit_store=catalog,
    )
    service._selected_model = lambda _key: _model()  # type: ignore[method-assign]
    return service


@pytest.mark.asyncio
async def test_invocation_audits_each_validated_attachment_before_provider_call() -> None:
    catalog, blobs = FakeFileCatalog(), FakeBlobStore()
    principal_id = str(uuid.uuid4())
    record = _ready(
        catalog,
        blobs,
        principal_id=principal_id,
        mime_type="image/png",
        data=b"image",
        name="secret-name.png",
    )
    service = _invocation_service(catalog, blobs, principal_id=principal_id)

    prepared = await service.prepare_invocation(
        provider_api_key="credential-must-not-be-audited",
        authorization="Bearer assertion-must-not-be-audited",
        body={
            "payload": {"prompt": "inspect", "attachments": [{"fileId": record.id}]},
            "correlationId": "corr-attach",
        },
    )

    assert prepared.attachments.records == (record,)
    event = catalog.audit_events[-1]
    assert event.action == "file.attached"
    assert event.actor_subject == "partner-user"
    assert event.file_id == record.id
    assert event.correlation_id == "corr-attach"
    serialized = repr(event.__dict__)
    assert "secret-name.png" not in serialized
    assert "credential-must-not-be-audited" not in serialized
    assert "assertion-must-not-be-audited" not in serialized


@pytest.mark.asyncio
async def test_attachment_audit_failure_aborts_prepared_invocation() -> None:
    catalog, blobs = FakeFileCatalog(), FakeBlobStore()
    principal_id = str(uuid.uuid4())
    record = _ready(
        catalog,
        blobs,
        principal_id=principal_id,
        mime_type="image/png",
        data=b"image",
    )
    service = _invocation_service(catalog, blobs, principal_id=principal_id)
    catalog.fail_audit = True

    with pytest.raises(FileAuditUnavailable):
        await service.prepare_invocation(
            provider_api_key="provider-key",
            authorization="Bearer assertion",
            body={
                "payload": {"prompt": "inspect", "attachments": [{"fileId": record.id}]}
            },
        )


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
    resolved = await AttachmentService(catalog, blobs).resolve_current(
        principal_id=principal_id,
        raw_attachments=[{"fileId": document.id}],
        model=_model(images=False),
    )
    assert resolved.records == (document,)


@pytest.mark.asyncio
async def test_document_attachment_materializes_persisted_text_for_text_only_model() -> None:
    catalog, blobs = FakeFileCatalog(), FakeBlobStore()
    processing = FakeFileProcessingStore(catalog)
    principal_id = str(uuid.uuid4())
    record = _ready(
        catalog,
        blobs,
        principal_id=principal_id,
        mime_type="application/pdf",
        data=b"original",
        name="report.pdf",
    )
    text = "[page=1]\nRevenue was 42."
    chunks = (
        '{"type":"chunk","id":"c1","text":"Revenue was 42.",'
        '"estimatedTokens":4,"ordinal":0,"provenance":{"page":1}}\n'
    ).encode()
    processing.add_derivative(
        blobs, record, kind="extracted_text", data=text.encode(), mime_type="text/plain"
    )
    processing.add_derivative(
        blobs, record, kind="chunks", data=chunks, mime_type="application/x-ndjson"
    )
    service = AttachmentService(catalog, blobs, processing_store=processing)
    current = await service.resolve_current(
        principal_id=principal_id,
        raw_attachments=[{"fileId": record.id}],
        model=_model(images=False),
    )
    message = PersistedUserMessage(
        content=[TextContent(text="What was revenue?"), *current.references], timestamp=1
    )

    output = await service.materialize(
        [message], principal_id=principal_id, model=_model(images=False), current=current
    )

    materialized = output[0].content[1]
    assert isinstance(materialized, TextContent)
    assert "Revenue was 42" in materialized.text
    assert "untrusted reference data" in materialized.text


@pytest.mark.asyncio
async def test_long_document_uses_query_relevant_chunk_within_budget() -> None:
    catalog, blobs = FakeFileCatalog(), FakeBlobStore()
    processing = FakeFileProcessingStore(catalog)
    principal_id = str(uuid.uuid4())
    record = _ready(
        catalog,
        blobs,
        principal_id=principal_id,
        mime_type="text/plain",
        data=b"original",
        name="long.txt",
    )
    full_text = "irrelevant filler " * 200
    chunks = (
        '{"type":"chunk","id":"c1","text":"unrelated appendix",'
        '"estimatedTokens":20,"ordinal":0,"provenance":{}}\n'
        '{"type":"chunk","id":"c2","text":"needle revenue equals 42",'
        '"estimatedTokens":20,"ordinal":1,"provenance":{"page":7}}\n'
    ).encode()
    processing.add_derivative(
        blobs, record, kind="extracted_text", data=full_text.encode(), mime_type="text/plain"
    )
    processing.add_derivative(
        blobs, record, kind="chunks", data=chunks, mime_type="application/x-ndjson"
    )
    service = AttachmentService(
        catalog,
        blobs,
        processing_store=processing,
        max_document_context_tokens=20,
    )
    current = await service.resolve_current(
        principal_id=principal_id,
        raw_attachments=[{"fileId": record.id}],
        model=_model(images=False),
    )
    message = PersistedUserMessage(
        content=[TextContent(text="Find the needle revenue"), *current.references], timestamp=1
    )

    output = await service.materialize(
        [message], principal_id=principal_id, model=_model(images=False), current=current
    )

    text = output[0].content[1]
    assert isinstance(text, TextContent)
    assert "needle revenue equals 42" in text.text
    assert "unrelated appendix" not in text.text
    assert "page=7" in text.text
    assert "mode=chunks" in text.text


@pytest.mark.asyncio
async def test_only_latest_historical_document_reference_is_rematerialized() -> None:
    catalog, blobs = FakeFileCatalog(), FakeBlobStore()
    processing = FakeFileProcessingStore(catalog)
    principal_id = str(uuid.uuid4())
    record = _ready(
        catalog,
        blobs,
        principal_id=principal_id,
        mime_type="text/plain",
        data=b"original",
        name="notes.txt",
    )
    processing.add_derivative(
        blobs,
        record,
        kind="extracted_text",
        data=b"persisted content",
        mime_type="text/plain",
    )
    processing.add_derivative(
        blobs,
        record,
        kind="chunks",
        data=(
            b'{"type":"chunk","id":"c1","text":"persisted content",'
            b'"estimatedTokens":4,"ordinal":0,"provenance":{}}\n'
        ),
        mime_type="application/x-ndjson",
    )
    reference = FileReferenceContent(
        fileId=record.id, mimeType=record.mime_type, displayName=record.original_name
    )
    messages = [
        PersistedUserMessage(content=[reference], timestamp=1),
        PersistedUserMessage(content=[reference], timestamp=2),
    ]

    output = await AttachmentService(
        catalog, blobs, processing_store=processing
    ).materialize(messages, principal_id=principal_id, model=_model(images=False))

    assert isinstance(output[0].content[0], TextContent)
    assert "superseded by newer reference" in output[0].content[0].text
    assert isinstance(output[1].content[0], TextContent)
    assert "persisted content" in output[1].content[0].text


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
