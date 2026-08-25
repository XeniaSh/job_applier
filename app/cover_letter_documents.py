from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re

from fpdf import FPDF


@dataclass(frozen=True)
class CoverLetterArtifacts:
    txt_path: Path | None
    pdf_path: Path | None
    pdf_error: str | None = None


def resolve_cover_letter_artifact_paths(*, base_dir: Path, source: str, external_id: str) -> tuple[Path, Path]:
    target_dir = base_dir / _safe_segment(source) / _safe_segment(external_id)
    return target_dir / "cover_letter.txt", target_dir / "cover_letter.pdf"


def generate_cover_letter_artifacts(
    *,
    base_dir: Path,
    source: str,
    external_id: str,
    candidate_name: str,
    language: str,
    cover_letter_text: str,
    font_path: Path | None = None,
) -> CoverLetterArtifacts:
    txt_path, pdf_path = resolve_cover_letter_artifact_paths(
        base_dir=base_dir,
        source=source,
        external_id=external_id,
    )
    target_dir = txt_path.parent
    target_dir.mkdir(parents=True, exist_ok=True)

    txt_path.write_text(cover_letter_text.strip() + "\n", encoding="utf-8")

    try:
        _render_cover_letter_pdf(
            output_path=pdf_path,
            candidate_name=candidate_name.strip(),
            language=language.strip().lower(),
            body=cover_letter_text.strip(),
            font_path=font_path,
        )
    except Exception as exc:  # noqa: BLE001
        return CoverLetterArtifacts(
            txt_path=txt_path,
            pdf_path=None,
            pdf_error=f"PDF generation failed: {exc}",
        )

    return CoverLetterArtifacts(txt_path=txt_path, pdf_path=pdf_path, pdf_error=None)


def _render_cover_letter_pdf(
    *,
    output_path: Path,
    candidate_name: str,
    language: str,
    body: str,
    font_path: Path | None,
) -> None:
    use_ru = language == "ru"
    today = datetime.now(timezone.utc).date().isoformat()
    greeting = "Команде по найму," if use_ru else "Dear Hiring Team,"
    closing = "С уважением," if use_ru else "Best regards,"
    header_lines = [today] if not candidate_name else [candidate_name, today]
    footer_lines = [closing] if not candidate_name else [closing, candidate_name]
    full_text = "\n".join([*header_lines, "", greeting, "", body, "", *footer_lines])

    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.set_margins(20, 20, 20)
    pdf.add_page()

    resolved_font = _resolve_font_path(font_path)
    has_non_ascii = any(ord(char) > 127 for char in full_text)
    if resolved_font is not None:
        pdf.add_font("CoverLetterFont", fname=str(resolved_font))
        font_family = "CoverLetterFont"
    elif has_non_ascii:
        raise ValueError("Unicode text requires COVER_LETTER_PDF_FONT_PATH.")
    else:
        font_family = "Helvetica"

    pdf.set_font(font_family, size=12)
    pdf.multi_cell(w=0, h=7, text=full_text)
    pdf.output(str(output_path))


def _resolve_font_path(font_path: Path | None) -> Path | None:
    if font_path is not None and font_path.exists() and font_path.is_file():
        return font_path.resolve()

    for candidate in (
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("C:/Windows/Fonts/calibri.ttf"),
        Path("C:/Windows/Fonts/segoeui.ttf"),
    ):
        if candidate.exists() and candidate.is_file():
            return candidate.resolve()
    return None


def _safe_segment(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_-]+", "_", value.strip())
    return normalized.strip("_") or "unknown"
