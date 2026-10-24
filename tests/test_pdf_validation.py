from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import patch

import pytest
from pypdf import PdfWriter

from app.pdf_validation import InvalidPdfError, is_valid_pdf_file, validate_pdf_file
from tests.pdf_fixtures import (
    MINIMAL_VALID_PDF_BYTES,
    write_corrupted_signature_pdf,
    write_forged_markers_only_pdf,
    write_minimal_valid_pdf,
)


def test_valid_synthetic_pdf_passes(tmp_path: Path) -> None:
    path = write_minimal_valid_pdf(tmp_path / "resume.pdf")
    validate_pdf_file(path)
    assert is_valid_pdf_file(path)


def test_missing_file_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "missing.pdf"
    with pytest.raises(InvalidPdfError):
        validate_pdf_file(path)
    assert not is_valid_pdf_file(path)


def test_empty_file_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "empty.pdf"
    path.write_bytes(b"")
    with pytest.raises(InvalidPdfError, match="empty"):
        validate_pdf_file(path)


def test_text_file_pretending_to_be_pdf_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "fake.pdf"
    path.write_text("Just a plain text resume, not a real PDF.\n", encoding="utf-8")
    with pytest.raises(InvalidPdfError, match="parsed"):
        validate_pdf_file(path)


def test_forged_markers_without_real_structure_is_rejected(tmp_path: Path) -> None:
    """A file that only contains the `%PDF-`/`endobj`/`trailer`/`%%EOF`
    substrings a naive validator would scan for, but no actual parseable
    object/page structure, must still be rejected.
    """
    path = write_forged_markers_only_pdf(tmp_path / "forged.pdf")
    with pytest.raises(InvalidPdfError):
        validate_pdf_file(path)
    assert not is_valid_pdf_file(path)


def test_valid_pdf_with_corrupted_signature_is_rejected(tmp_path: Path) -> None:
    """A byte-for-byte valid, parseable PDF structure is still rejected when
    its leading `%PDF-` signature has been replaced (e.g. with `TEXT-`) --
    `pypdf`'s lenient (`strict=False`) parser tolerates a missing/wrong
    header and parses the rest of the structure anyway, so the signature
    itself must be checked explicitly rather than left to `pypdf`.
    """
    path = write_corrupted_signature_pdf(tmp_path / "corrupted_signature.pdf")
    with pytest.raises(InvalidPdfError, match="parsed"):
        validate_pdf_file(path)
    assert not is_valid_pdf_file(path)


def test_truncated_pdf_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "truncated.pdf"
    path.write_bytes(MINIMAL_VALID_PDF_BYTES[: len(MINIMAL_VALID_PDF_BYTES) // 2])
    with pytest.raises(InvalidPdfError):
        validate_pdf_file(path)


def test_directory_path_is_rejected(tmp_path: Path) -> None:
    directory = tmp_path / "not_a_file.pdf"
    directory.mkdir()
    with pytest.raises(InvalidPdfError):
        validate_pdf_file(directory)


def test_unreadable_file_is_rejected(tmp_path: Path) -> None:
    path = write_minimal_valid_pdf(tmp_path / "resume.pdf")
    with patch("pathlib.Path.read_bytes", side_effect=PermissionError("denied")):
        with pytest.raises(InvalidPdfError, match="could not be read"):
            validate_pdf_file(path)


def test_encrypted_pdf_is_rejected(tmp_path: Path) -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.encrypt(user_password="secret")
    buffer = io.BytesIO()
    writer.write(buffer)
    path = tmp_path / "encrypted.pdf"
    path.write_bytes(buffer.getvalue())
    with pytest.raises(InvalidPdfError, match="encrypted"):
        validate_pdf_file(path)


def test_zero_page_pdf_is_rejected(tmp_path: Path) -> None:
    writer = PdfWriter()
    buffer = io.BytesIO()
    writer.write(buffer)
    path = tmp_path / "zero_pages.pdf"
    path.write_bytes(buffer.getvalue())
    with pytest.raises(InvalidPdfError, match="no pages"):
        validate_pdf_file(path)
