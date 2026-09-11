from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path

import pytest

from app.application.artifact_retention import (
    ArtifactRetentionError,
    cleanup_generated_artifacts,
)


def test_deletes_old_cover_letters_and_keeps_recent(tmp_path: Path) -> None:
    now = datetime(2026, 9, 10, tzinfo=timezone.utc)
    old_dir = tmp_path / "linkedin-email" / "101"
    new_dir = tmp_path / "linkedin-email" / "202"
    old_dir.mkdir(parents=True)
    new_dir.mkdir(parents=True)
    old_txt = old_dir / "cover_letter.txt"
    old_pdf = old_dir / "cover_letter.pdf"
    new_txt = new_dir / "cover_letter.txt"
    extra = old_dir / "notes.txt"
    old_txt.write_text("old", encoding="utf-8")
    old_pdf.write_text("old-pdf", encoding="utf-8")
    new_txt.write_text("new", encoding="utf-8")
    extra.write_text("keep", encoding="utf-8")
    old_stamp = (now - timedelta(days=20)).timestamp()
    new_stamp = (now - timedelta(days=2)).timestamp()
    os.utime(old_txt, (old_stamp, old_stamp))
    os.utime(old_pdf, (old_stamp, old_stamp))
    os.utime(new_txt, (new_stamp, new_stamp))

    report = cleanup_generated_artifacts(tmp_path, max_age_days=14, now=now)

    assert old_txt.exists() is False
    assert old_pdf.exists() is False
    assert new_txt.exists() is True
    assert extra.exists() is True
    assert extra in report.skipped_files


def test_refuses_to_clean_resume_directory(tmp_path: Path) -> None:
    resumes = tmp_path / "resumes"
    resumes.mkdir()
    original = resumes / "java_backend.pdf"
    original.write_bytes(b"%PDF-FAKE\n")
    with pytest.raises(ArtifactRetentionError, match="resume directory"):
        cleanup_generated_artifacts(resumes, max_age_days=14)
    assert original.exists()
