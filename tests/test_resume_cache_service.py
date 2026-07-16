import os
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.application.resume_cache_service import ResumeCacheService, _resolve_resume_path
from app.storage.telegram_delivery import TelegramDeliveryStorage
from app.telegram.models import TelegramDocumentRef


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


def _create_service(tmp_path: Path):
    resumes_dir = tmp_path / "resumes"
    resumes_dir.mkdir(parents=True, exist_ok=True)
    storage = TelegramDeliveryStorage(db_path=tmp_path / "jobs.db")
    fake_client = _FakeTelegramClient(calls=[])
    service = ResumeCacheService(
        resumes_dir=resumes_dir,
        storage=storage,
        telegram_client=fake_client,  # type: ignore[arg-type]
    )
    return service, storage, resumes_dir, fake_client


def test_first_use_uploads_and_saves_cache(tmp_path: Path) -> None:
    service, storage, resumes_dir, fake = _create_service(tmp_path)
    pdf = resumes_dir / "java-backend.pdf"
    pdf.write_bytes(b"%PDF one")

    result = service.get_or_upload(resume_name="java-backend", chat_id="123")
    assert result.uploaded is True
    assert result.cache_hit is False
    assert result.missing is False
    assert len(fake.calls) == 1
    cached = storage.get_resume_cache("java-backend")
    assert cached is not None
    assert cached.telegram_file_id == result.telegram_file_id


def test_second_use_reuses_file_id_cache_hit(tmp_path: Path) -> None:
    service, _storage, resumes_dir, fake = _create_service(tmp_path)
    pdf = resumes_dir / "java-backend.pdf"
    pdf.write_bytes(b"%PDF one")
    first = service.get_or_upload(resume_name="java-backend", chat_id="123")
    second = service.get_or_upload(resume_name="java-backend", chat_id="123")
    assert first.telegram_file_id == second.telegram_file_id
    assert second.cache_hit is True
    assert second.uploaded is False
    assert len(fake.calls) == 1


def test_changed_mtime_invalidates_cache(tmp_path: Path) -> None:
    service, _storage, resumes_dir, fake = _create_service(tmp_path)
    pdf = resumes_dir / "java-backend.pdf"
    pdf.write_bytes(b"%PDF one")
    service.get_or_upload(resume_name="java-backend", chat_id="123")
    st = pdf.stat()
    os.utime(pdf, ns=(st.st_atime_ns, st.st_mtime_ns + 1000))
    result = service.get_or_upload(resume_name="java-backend", chat_id="123")
    assert result.uploaded is True
    assert len(fake.calls) == 2


def test_changed_file_size_invalidates_cache(tmp_path: Path) -> None:
    service, storage, resumes_dir, fake = _create_service(tmp_path)
    pdf = resumes_dir / "java-backend.pdf"
    pdf.write_bytes(b"%PDF one")
    service.get_or_upload(resume_name="java-backend", chat_id="123")
    cached = storage.get_resume_cache("java-backend")
    assert cached is not None

    pdf.write_bytes(b"%PDF changed and bigger")
    os.utime(pdf, ns=(cached.file_mtime_ns, cached.file_mtime_ns))
    result = service.get_or_upload(resume_name="java-backend", chat_id="123")
    assert result.uploaded is True
    assert len(fake.calls) == 2


def test_missing_pdf_returns_missing_without_failure(tmp_path: Path) -> None:
    service, _storage, _resumes_dir, fake = _create_service(tmp_path)
    result = service.get_or_upload(resume_name="java-backend", chat_id="123")
    assert result.missing is True
    assert result.telegram_file_id is None
    assert len(fake.calls) == 0


def test_path_traversal_rejected(tmp_path: Path) -> None:
    resumes_dir = tmp_path / "resumes"
    resumes_dir.mkdir()
    with pytest.raises(ValueError):
        _resolve_resume_path(resumes_dir, "../secrets")
