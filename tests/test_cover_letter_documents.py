from pathlib import Path

from app.cover_letter_documents import generate_cover_letter_artifacts


def test_generate_cover_letter_artifacts_writes_txt_and_pdf(tmp_path: Path) -> None:
    result = generate_cover_letter_artifacts(
        base_dir=tmp_path / "prepared",
        source="linkedin-email",
        external_id="job-1",
        candidate_name="Jane Doe",
        language="en",
        cover_letter_text="I can help with backend delivery.",
    )

    assert result.txt_path is not None
    assert result.pdf_path is not None
    assert result.pdf_error is None
    assert result.txt_path.read_text(encoding="utf-8").strip() == "I can help with backend delivery."
    assert result.pdf_path.read_bytes().startswith(b"%PDF")


def test_generate_cover_letter_artifacts_skips_pdf_when_name_missing(tmp_path: Path) -> None:
    result = generate_cover_letter_artifacts(
        base_dir=tmp_path / "prepared",
        source="linkedin-email",
        external_id="job-2",
        candidate_name="",
        language="en",
        cover_letter_text="Plain letter.",
    )

    assert result.txt_path is not None
    assert result.pdf_path is None
    assert result.pdf_error is not None


def test_generate_cover_letter_artifacts_handles_unicode_font_failure(tmp_path: Path, monkeypatch) -> None:
    from app import cover_letter_documents as module

    monkeypatch.setattr(module, "_resolve_font_path", lambda _: None)
    result = generate_cover_letter_artifacts(
        base_dir=tmp_path / "prepared",
        source="linkedin-email",
        external_id="job-3",
        candidate_name="Иван Иванов",
        language="ru",
        cover_letter_text="Опыт в Java и микросервисах.",
        font_path=tmp_path / "missing.ttf",
    )

    assert result.txt_path is not None
    assert result.pdf_path is None
    assert "Unicode text requires" in (result.pdf_error or "")
