"""Local document detection, extraction, and normalization implementations."""

from agent_smith.infra.document_processing.detector import SupportedFileTypeDetector
from agent_smith.infra.document_processing.processors import (
    CsvProcessor,
    DocxProcessor,
    MarkdownProcessor,
    PdfProcessor,
    PlainTextProcessor,
    ProcessorRegistry,
    XlsxProcessor,
    inspect_image,
)


def create_processor_registry() -> ProcessorRegistry:
    return ProcessorRegistry(
        [
            PlainTextProcessor(),
            MarkdownProcessor(),
            CsvProcessor(),
            PdfProcessor(),
            DocxProcessor(),
            XlsxProcessor(),
        ]
    )


__all__ = [
    "SupportedFileTypeDetector",
    "create_processor_registry",
    "inspect_image",
]
