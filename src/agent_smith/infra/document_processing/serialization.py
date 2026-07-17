"""Stable derivative serialization and deterministic chunking."""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from dataclasses import asdict
from typing import Any

from agent_smith.app.ports.document_processing import (
    GeneratedArtifact,
    NormalizedBlock,
    NormalizedDocument,
)

CHUNK_TARGET_CHARS = 4_800
CHUNK_OVERLAP_CHARS = 600


def build_core_artifacts(document: NormalizedDocument) -> tuple[GeneratedArtifact, ...]:
    normalized = serialize_normalized_document(document)
    extracted = format_extracted_text(document).encode("utf-8")
    chunks = serialize_chunks(document)
    return (
        GeneratedArtifact(
            kind="normalized_document",
            mime_type="application/x-ndjson",
            data=normalized,
            metadata={"schemaVersion": document.schema_version, "sha256": _sha(normalized)},
        ),
        GeneratedArtifact(
            kind="extracted_text",
            mime_type="text/plain",
            data=extracted,
            metadata={"characterCount": len(extracted.decode("utf-8")), "sha256": _sha(extracted)},
        ),
        GeneratedArtifact(
            kind="chunks",
            mime_type="application/x-ndjson",
            data=chunks,
            metadata={"schemaVersion": "1", "sha256": _sha(chunks)},
        ),
    )


def serialize_normalized_document(document: NormalizedDocument) -> bytes:
    header = {
        "type": "document",
        "schemaVersion": document.schema_version,
        "fileId": document.file_id,
        "detectedMimeType": document.detected_mime_type,
        "metadata": document.metadata,
    }
    lines = [_json(header)]
    for block in document.blocks:
        payload = asdict(block)
        payload["type"] = "block"
        lines.append(_json(payload))
    return ("\n".join(lines) + "\n").encode("utf-8")


def format_extracted_text(document: NormalizedDocument) -> str:
    parts: list[str] = []
    for block in document.blocks:
        label = _source_label(block)
        body = _block_text(block)
        if body:
            parts.append(f"[{label}]\n{body}" if label else body)
    return "\n\n".join(parts).strip()


def serialize_chunks(document: NormalizedDocument) -> bytes:
    chunks: list[dict[str, Any]] = []
    for block in document.blocks:
        text = _block_text(block)
        if not text:
            continue
        start = 0
        while start < len(text):
            end = min(len(text), start + CHUNK_TARGET_CHARS)
            if end < len(text):
                split = text.rfind("\n", start, end)
                if split > start + CHUNK_TARGET_CHARS // 2:
                    end = split
            content = text[start:end].strip()
            if content:
                chunks.append(
                    {
                        "type": "chunk",
                        "schemaVersion": "1",
                        "id": f"c{len(chunks) + 1:06d}",
                        "fileId": document.file_id,
                        "text": content,
                        "estimatedTokens": math.ceil(len(content) / 4),
                        "blockIds": [block.id],
                        "ordinal": len(chunks),
                        "sourceOrdinal": block.ordinal,
                        "provenance": asdict(block.provenance),
                    }
                )
            if end >= len(text):
                break
            start = max(start + 1, end - CHUNK_OVERLAP_CHARS)
    return ("\n".join(_json(item) for item in chunks) + ("\n" if chunks else "")).encode(
        "utf-8"
    )


def artifact_identity(
    *, file_id: str, pipeline_version: str, artifact: GeneratedArtifact
) -> tuple[str, str]:
    digest = hashlib.sha256(artifact.data).hexdigest()
    extension = {
        "application/x-ndjson": "jsonl",
        "text/plain": "txt",
        "image/png": "png",
        "image/jpeg": "jpg",
    }.get(artifact.mime_type, "bin")
    object_key = (
        f"files/{file_id}/derivatives/{pipeline_version}/{artifact.kind}/{digest}.{extension}"
    )
    derivative_id = str(uuid.uuid5(uuid.NAMESPACE_URL, object_key))
    return derivative_id, object_key


def parse_jsonl(data: bytes) -> list[dict[str, Any]]:
    try:
        return [json.loads(line) for line in data.decode("utf-8").splitlines() if line.strip()]
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Derivative JSONL is corrupt") from exc


def _block_text(block: NormalizedBlock) -> str:
    if block.text is not None:
        prefix = "# " if block.kind == "heading" else ""
        return prefix + block.text
    if block.table is None:
        return ""
    rows = block.table.rows
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    normalized = [tuple([*row, *([""] * (width - len(row)))]) for row in rows]
    lines = ["| " + " | ".join(_escape_cell(cell) for cell in row) + " |" for row in normalized]
    if block.table.header_rows:
        lines.insert(1, "| " + " | ".join("---" for _ in range(width)) + " |")
    return "\n".join(lines)


def _source_label(block: NormalizedBlock) -> str:
    source: list[str] = []
    if block.provenance.page is not None:
        source.append(f"page={block.provenance.page}")
    if block.provenance.sheet is not None:
        source.append(f"sheet={block.provenance.sheet}")
    if block.provenance.cell_range is not None:
        source.append(f"range={block.provenance.cell_range}")
    if block.provenance.section:
        source.append(f"section={' > '.join(block.provenance.section)}")
    return ", ".join(source)


def _escape_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", "<br>")


def _json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
