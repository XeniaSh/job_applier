import os
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.application.resume_cache_service import ResumeCacheService
from app.resume_profiles import ResumeProfilesLoadError
from app.storage.telegram_delivery import TelegramDeliveryStorage
from app.telegram.models import TelegramDocumentRef
from tests.pdf_fixtures import MINIMAL_VALID_PDF_BYTES, write_alternate_valid_pdf, write_minimal_valid_pdf


@dataclass
class _FakeTelegramClient:
    calls: list[tuple[str, str]]

    def send_document(self, *, file_path: str, caption: str) -> TelegramDocumentRef:
        self.calls.append((file_path, caption))
        return TelegramDocumentRef(
            chat_id="123",
            message_id=100 + len(self.calls),
            file_id=f"FILE_ID_{len(self.calls)}",
            file_unique_id=f"UNIQ_{len(self.calls)}",
        )


def _create_service(tmp_path: Path, resume_paths: dict | None = None):
    resumes_dir = tmp_path / "resumes"
    resumes_dir.mkdir(parents=True, exist_ok=True)
    storage = TelegramDeliveryStorage(db_path=tmp_path / "jobs.db")
    fake_client = _FakeTelegramClient(calls=[])
    service = ResumeCacheService(
        resumes_dir=resumes_dir,
        storage=storage,
        telegram_client=fake_client,  # type: ignore[arg-type]
        resume_paths=resume_paths if resume_paths is not None else {"java": resumes_dir / "java-backend.pdf"},
    )
    return service, storage, resumes_dir, fake_client


def test_first_use_uploads_and_saves_cache(tmp_path: Path) -> None:
    service, storage, resumes_dir, fake = _create_service(tmp_path)
    pdf = resumes_dir / "java-backend.pdf"
    write_minimal_valid_pdf(pdf)

    result = service.get_or_upload(resume_name="java", chat_id="123")
    assert result.uploaded is True
    assert result.cache_hit is False
    assert result.missing is False
    assert len(fake.calls) == 1
    cached = storage.get_resume_cache("java_backend")
    assert cached is not None
    assert cached.telegram_file_id == result.telegram_file_id


def test_second_use_reuses_file_id_cache_hit(tmp_path: Path) -> None:
    service, _storage, resumes_dir, fake = _create_service(tmp_path)
    pdf = resumes_dir / "java-backend.pdf"
    write_minimal_valid_pdf(pdf)
    first = service.get_or_upload(resume_name="java", chat_id="123")
    second = service.get_or_upload(resume_name="java", chat_id="123")
    assert first.telegram_file_id == second.telegram_file_id
    assert second.cache_hit is True
    assert second.uploaded is False
    assert len(fake.calls) == 1


def test_changed_mtime_invalidates_cache(tmp_path: Path) -> None:
    service, _storage, resumes_dir, fake = _create_service(tmp_path)
    pdf = resumes_dir / "java-backend.pdf"
    write_minimal_valid_pdf(pdf)
    service.get_or_upload(resume_name="java", chat_id="123")
    st = pdf.stat()
    os.utime(pdf, ns=(st.st_atime_ns, st.st_mtime_ns + 1000))
    result = service.get_or_upload(resume_name="java", chat_id="123")
    assert result.uploaded is True
    assert len(fake.calls) == 2


def test_changed_file_size_invalidates_cache(tmp_path: Path) -> None:
    service, storage, resumes_dir, fake = _create_service(tmp_path)
    pdf = resumes_dir / "java-backend.pdf"
    write_minimal_valid_pdf(pdf)
    service.get_or_upload(resume_name="java", chat_id="123")
    cached = storage.get_resume_cache("java_backend")
    assert cached is not None

    write_alternate_valid_pdf(pdf)
    os.utime(pdf, ns=(cached.file_mtime_ns, cached.file_mtime_ns))
    result = service.get_or_upload(resume_name="java", chat_id="123")
    assert result.uploaded is True
    assert len(fake.calls) == 2


def test_invalid_pdf_rejected_even_with_unchanged_cache_stat(tmp_path: Path) -> None:
    """A cache hit is normally decided from path/mtime/size alone -- but an
    in-place content corruption that keeps the same mtime and size must
    still be caught, since the file is validated as a real PDF on every
    call, not just on first upload.
    """
    service, storage, resumes_dir, fake = _create_service(tmp_path)
    pdf = resumes_dir / "java-backend.pdf"
    write_minimal_valid_pdf(pdf)
    first = service.get_or_upload(resume_name="java", chat_id="123")
    assert first.uploaded is True
    cached = storage.get_resume_cache("java_backend")
    assert cached is not None

    garbage = b"X" * len(MINIMAL_VALID_PDF_BYTES)
    pdf.write_bytes(garbage)
    os.utime(pdf, ns=(cached.file_mtime_ns, cached.file_mtime_ns))
    assert pdf.stat().st_size == cached.file_size

    result = service.get_or_upload(resume_name="java", chat_id="123")
    assert result.missing is True
    assert result.telegram_file_id is None
    assert result.cache_hit is False
    assert result.warning is not None
    assert "java_backend" in result.warning
    assert len(fake.calls) == 1


def test_missing_pdf_returns_missing_without_failure(tmp_path: Path) -> None:
    service, _storage, _resumes_dir, fake = _create_service(tmp_path)
    result = service.get_or_upload(resume_name="java", chat_id="123")
    assert result.missing is True
    assert result.telegram_file_id is None
    assert len(fake.calls) == 0
    assert result.warning is not None
    assert "java_backend" in result.warning
    assert "java-backend.pdf" in result.warning


def test_configured_profile_id_uses_configured_pdf_path(tmp_path: Path) -> None:
    resumes_dir = tmp_path / "resumes"
    resumes_dir.mkdir()
    pdf = resumes_dir / "java_backend_ai.pdf"
    write_minimal_valid_pdf(pdf)
    storage = TelegramDeliveryStorage(db_path=tmp_path / "jobs.db")
    fake_client = _FakeTelegramClient(calls=[])
    service = ResumeCacheService(
        resumes_dir=resumes_dir,
        storage=storage,
        telegram_client=fake_client,  # type: ignore[arg-type]
        resume_paths={"java_ai": pdf},
    )

    result = service.get_or_upload(resume_name="java_ai", chat_id="123")

    assert result.uploaded is True
    assert result.resume_name == "ai_backend"
    assert result.resume_path == str(pdf.resolve())


def test_legacy_alias_name_resolves_configured_path_via_canonical_lookup(tmp_path: Path) -> None:
    """A legacy-id-configured profile (`java`) must still be found when the
    caller requests it by its canonical id (`java_backend`) -- the lookup is
    bidirectional, never a guess at a different variant's file.
    """
    service, storage, resumes_dir, fake = _create_service(tmp_path)
    pdf = resumes_dir / "java-backend.pdf"
    write_minimal_valid_pdf(pdf)

    result = service.get_or_upload(resume_name="java_backend", chat_id="123")

    assert result.uploaded is True
    assert result.resume_name == "java_backend"
    assert result.resume_path == str(pdf.resolve())
    assert len(fake.calls) == 1


def test_no_resume_paths_loads_configured_resume_profiles(tmp_path: Path) -> None:
    """With `resume_paths=None` (no explicit map supplied at all), the
    service must load the normal `resume_profiles.yaml` configuration via
    `load_resume_profiles` -- resolving to whatever PDF path is configured
    there, including a hyphenated filename that the old
    `resumes_dir/<canonical_name>.pdf` guessing convention would never
    match -- never guessing a filename itself.
    """
    resumes_dir = tmp_path / "resumes"
    resumes_dir.mkdir()
    pdf = resumes_dir / "java-backend-configured.pdf"
    write_minimal_valid_pdf(pdf)
    profiles_path = tmp_path / "resume_profiles.yaml"
    profiles_path.write_text(
        "profiles:\n"
        "  - id: java_backend\n"
        "    description: Java backend resume.\n"
        f"    pdf: {pdf}\n",
        encoding="utf-8",
    )
    storage = TelegramDeliveryStorage(db_path=tmp_path / "jobs.db")
    fake_client = _FakeTelegramClient(calls=[])
    service = ResumeCacheService(
        resumes_dir=resumes_dir,
        storage=storage,
        telegram_client=fake_client,  # type: ignore[arg-type]
        resume_profiles_path=profiles_path,
    )

    result = service.get_or_upload(resume_name="java_backend", chat_id="123")

    assert result.uploaded is True
    assert result.resume_path == str(pdf.resolve())
    assert len(fake_client.calls) == 1


def test_no_resume_paths_with_broken_config_raises_visibly(tmp_path: Path) -> None:
    """A config-load failure (missing/invalid `resume_profiles.yaml`) must
    surface as a visible, actionable error from construction -- never be
    swallowed and never fall back to directory-guessing.
    """
    resumes_dir = tmp_path / "resumes"
    resumes_dir.mkdir()
    storage = TelegramDeliveryStorage(db_path=tmp_path / "jobs.db")
    fake_client = _FakeTelegramClient(calls=[])

    with pytest.raises(ResumeProfilesLoadError):
        ResumeCacheService(
            resumes_dir=resumes_dir,
            storage=storage,
            telegram_client=fake_client,  # type: ignore[arg-type]
            resume_profiles_path=tmp_path / "missing_resume_profiles.yaml",
        )


def test_explicit_incomplete_map_never_falls_back_to_guessed_file_even_if_valid(tmp_path: Path) -> None:
    """An explicitly supplied `resume_paths` map (even one that simply omits
    a variant) is authoritative: the omitted variant must report
    `missing=True` even when a valid PDF happens to exist at the old
    `resumes_dir/<canonical_name>.pdf` guessing convention's path, since
    filenames are never guessed.
    """
    resumes_dir = tmp_path / "resumes"
    resumes_dir.mkdir()
    guessed_pdf = resumes_dir / "java_backend.pdf"
    write_minimal_valid_pdf(guessed_pdf)
    storage = TelegramDeliveryStorage(db_path=tmp_path / "jobs.db")
    fake_client = _FakeTelegramClient(calls=[])
    service = ResumeCacheService(
        resumes_dir=resumes_dir,
        storage=storage,
        telegram_client=fake_client,  # type: ignore[arg-type]
        resume_paths={"ai_backend": resumes_dir / "ai-backend.pdf"},
    )

    result = service.get_or_upload(resume_name="java_backend", chat_id="123")

    assert result.missing is True
    assert result.resume_path is None
    assert result.telegram_file_id is None
    assert result.warning is not None
    assert "java_backend" in result.warning
    assert len(fake_client.calls) == 0


def test_unconfigured_variant_never_falls_back_to_another_variants_file(tmp_path: Path) -> None:
    """Only `ai_backend` is configured; requesting the unconfigured
    `java_backend` must never fall back to the `ai_backend` PDF -- it must
    report missing, naming the requested variant.
    """
    resumes_dir = tmp_path / "resumes"
    resumes_dir.mkdir()
    ai_pdf = resumes_dir / "ai-backend.pdf"
    write_minimal_valid_pdf(ai_pdf)
    storage = TelegramDeliveryStorage(db_path=tmp_path / "jobs.db")
    fake_client = _FakeTelegramClient(calls=[])
    service = ResumeCacheService(
        resumes_dir=resumes_dir,
        storage=storage,
        telegram_client=fake_client,  # type: ignore[arg-type]
        resume_paths={"ai_backend": ai_pdf},
    )

    result = service.get_or_upload(resume_name="java_backend", chat_id="123")

    assert result.missing is True
    assert result.telegram_file_id is None
    assert result.resume_path is None
    assert result.warning is not None
    assert "java_backend" in result.warning
    assert len(fake_client.calls) == 0
