"""Shared synthetic PDF fixtures for tests that must pass structural PDF
validation (see `app.pdf_validation`). Built with `fpdf2` -- a real,
parseable one-page PDF, never a personal resume and never a forged header
with no actual page content.
"""

from __future__ import annotations

from pathlib import Path

from fpdf import FPDF


def _build_minimal_valid_pdf() -> bytes:
    pdf = FPDF(format="A4")
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.cell(text="Synthetic test resume -- not a real document.")
    return bytes(pdf.output())


def _build_alternate_valid_pdf() -> bytes:
    """A second real, parseable one-page PDF with different content/size --
    used by cache-invalidation tests that need two distinct valid PDFs
    rather than a valid one and a forged one.
    """
    pdf = FPDF(format="A4")
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.cell(text="Synthetic test resume, alternate revision -- not a real document.")
    return bytes(pdf.output())


MINIMAL_VALID_PDF_BYTES = _build_minimal_valid_pdf()
ALTERNATE_VALID_PDF_BYTES = _build_alternate_valid_pdf()

# A forged file that merely contains the marker substrings a naive
# substring-based validator would look for, with no real PDF object/page
# structure. Used to prove the validator actually parses the file instead of
# scanning for these markers.
FORGED_MARKERS_ONLY_BYTES = b"%PDF-1.7\nendobj\ntrailer\n%%EOF"

# A byte-for-byte valid, parseable one-page PDF whose leading `%PDF-`
# signature has been swapped for another same-length marker, leaving every
# other byte offset (and thus the structure `pypdf` scans for) untouched.
# Used to prove the validator checks the PDF signature itself rather than
# relying on `pypdf`'s lenient (`strict=False`) parser, which tolerates a
# missing/wrong header and parses the rest of the file anyway.
CORRUPTED_SIGNATURE_PDF_BYTES = b"TEXT-" + MINIMAL_VALID_PDF_BYTES[len(b"%PDF-") :]


def write_minimal_valid_pdf(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(MINIMAL_VALID_PDF_BYTES)
    return path


def write_alternate_valid_pdf(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(ALTERNATE_VALID_PDF_BYTES)
    return path


def write_forged_markers_only_pdf(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(FORGED_MARKERS_ONLY_BYTES)
    return path


def write_corrupted_signature_pdf(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(CORRUPTED_SIGNATURE_PDF_BYTES)
    return path
