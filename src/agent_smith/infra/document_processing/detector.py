"""Content-first detector for the bounded Milestone 4 format set."""

from __future__ import annotations

import csv
import io
import zipfile
from pathlib import PurePath

from agent_smith.app.ports.document_processing import (
    DetectedFile,
    FileProcessingError,
)

OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
MAX_ZIP_ENTRIES = 10_000
MAX_ZIP_UNCOMPRESSED_BYTES = 256 * 1024 * 1024
MAX_ZIP_ENTRY_BYTES = 64 * 1024 * 1024


class SupportedFileTypeDetector:
    def detect(self, *, data: bytes, declared_mime_type: str, filename: str) -> DetectedFile:
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            return DetectedFile("image/png")
        if data.startswith(b"\xff\xd8\xff"):
            return DetectedFile("image/jpeg")
        if data.startswith((b"GIF87a", b"GIF89a")):
            return DetectedFile("image/gif")
        if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
            return DetectedFile("image/webp")
        if data.startswith(b"%PDF-"):
            return DetectedFile("application/pdf")
        if data.startswith(OLE_MAGIC):
            raise FileProcessingError(
                "unsupported_legacy_format",
                "Legacy DOC/XLS processing is not supported.",
                retryable=False,
            )
        if data.startswith(b"PK\x03\x04"):
            return self._detect_ooxml(data)
        if b"\x00" in data:
            raise FileProcessingError(
                "unsupported_file_type", "File content is not a supported format.", retryable=False
            )
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise FileProcessingError(
                "unsupported_encoding", "Text documents must use UTF-8.", retryable=False
            ) from exc
        suffix = PurePath(filename).suffix.lower()
        if declared_mime_type == "text/csv" or self._looks_like_csv(text):
            return DetectedFile("text/csv")
        if declared_mime_type == "text/markdown" or suffix in {".md", ".markdown"}:
            return DetectedFile("text/markdown")
        return DetectedFile("text/plain")

    def _detect_ooxml(self, data: bytes) -> DetectedFile:
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                infos = archive.infolist()
                if len(infos) > MAX_ZIP_ENTRIES:
                    raise self._zip_bomb()
                total = 0
                names: set[str] = set()
                for info in infos:
                    total += info.file_size
                    if info.file_size > MAX_ZIP_ENTRY_BYTES or total > MAX_ZIP_UNCOMPRESSED_BYTES:
                        raise self._zip_bomb()
                    if (
                        info.compress_size > 0
                        and info.file_size > 10 * 1024 * 1024
                        and info.file_size / info.compress_size > 200
                    ):
                        raise self._zip_bomb()
                    names.add(info.filename)
        except FileProcessingError:
            raise
        except (zipfile.BadZipFile, OSError) as exc:
            raise FileProcessingError(
                "corrupt_document", "OOXML container is corrupt.", retryable=False
            ) from exc
        if "word/document.xml" in names:
            return DetectedFile(
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            )
        if "xl/workbook.xml" in names:
            return DetectedFile(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
        raise FileProcessingError(
            "unsupported_file_type", "ZIP content is not a supported OOXML document.", retryable=False
        )

    @staticmethod
    def _looks_like_csv(text: str) -> bool:
        sample = text[:8192]
        if "\n" not in sample or not any(char in sample for char in ",;\t"):
            return False
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
            rows = list(csv.reader(io.StringIO(sample), dialect))[:5]
        except csv.Error:
            return False
        widths = [len(row) for row in rows if row]
        return len(widths) >= 2 and min(widths) > 1 and len(set(widths)) <= 2

    @staticmethod
    def _zip_bomb() -> FileProcessingError:
        return FileProcessingError(
            "zip_bomb_detected", "OOXML expansion exceeds safe limits.", retryable=False
        )
