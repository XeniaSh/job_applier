"""Structural PDF validation via `pypdf`.

Used wherever a configured resume PDF must be proven to actually be a usable
PDF before it is attached to an ATS form or delivered to Telegram -- not just
`Path.is_file()` or a `.pdf` extension check, and never a forged header with
no real page content. Validation means: the file parses as a PDF, is not
password-encrypted, and has at least one real page -- see
https://pypdf.readthedocs.io/en/stable/modules/PdfReader.html.
"""

from __future__ import annotations

import io
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError

# `pypdf`'s lenient (`strict=False`) reader tolerates a missing/corrupted
# `%PDF-` header and will still parse the rest of a structurally valid file
# by scanning for `startxref`/`trailer` -- so the signature must be checked
# explicitly here rather than left to `PdfReader` to reject.
_PDF_SIGNATURE = b"%PDF-"


class InvalidPdfError(Exception):
    """Raised when a path does not point to a usable, non-encrypted PDF file."""


def validate_pdf_file(path: Path) -> None:
    """Raise `InvalidPdfError` unless `path` is a non-empty file that `pypdf`
    can parse as a non-encrypted PDF with at least one page. Raises for a
    missing/unreadable file too, so callers never need a separate
    `is_file()` check first.
    """
    try:
        if not path.is_file():
            raise InvalidPdfError(f"Resume PDF does not exist or is not a regular file: {path}")
        data = path.read_bytes()
    except OSError as exc:
        raise InvalidPdfError(f"Resume PDF could not be read: {path} ({exc})") from exc

    if not data:
        raise InvalidPdfError(f"Resume PDF is empty: {path}")

    if not data.startswith(_PDF_SIGNATURE):
        raise InvalidPdfError(f"Resume PDF could not be parsed (missing PDF signature): {path}")

    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
    except (PdfReadError, ValueError, OSError) as exc:
        raise InvalidPdfError(f"Resume PDF could not be parsed (corrupt or truncated?): {path} ({exc})") from exc

    if reader.is_encrypted:
        raise InvalidPdfError(f"Resume PDF is password-encrypted and cannot be used: {path}")

    try:
        page_count = len(reader.pages)
    except (PdfReadError, ValueError) as exc:
        raise InvalidPdfError(f"Resume PDF has no readable pages (corrupt?): {path} ({exc})") from exc

    if page_count < 1:
        raise InvalidPdfError(f"Resume PDF has no pages: {path}")


def is_valid_pdf_file(path: Path) -> bool:
    """Convenience boolean wrapper around `validate_pdf_file`."""
    try:
        validate_pdf_file(path)
    except InvalidPdfError:
        return False
    return True
