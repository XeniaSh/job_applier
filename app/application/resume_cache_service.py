from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.models import RecommendedResume
from app.storage.telegram_delivery import TelegramDeliveryStorage
from app.telegram.client import TelegramClient

KNOWN_RESUME_NAMES: tuple[str, ...] = tuple(item.value for item in RecommendedResume)


@dataclass(frozen=True)
class ResumeDeliveryResult:
    resume_name: str
    resume_path: str | None
    telegram_file_id: str | None
    cache_hit: bool
    uploaded: bool
    missing: bool


class ResumeCacheService:
    def __init__(
        self,
        *,
        resumes_dir: Path,
        storage: TelegramDeliveryStorage,
        telegram_client: TelegramClient,
    ) -> None:
        self._resumes_dir = resumes_dir
        self._storage = storage
        self._telegram_client = telegram_client

    def get_or_upload(
        self,
        *,
        resume_name: str,
        chat_id: str,
        force_upload: bool = False,
    ) -> ResumeDeliveryResult:
        normalized_name = _normalize_resume_name(resume_name)
        path = _resolve_resume_path(self._resumes_dir, normalized_name)
        if path is None:
            return ResumeDeliveryResult(
                resume_name=normalized_name,
                resume_path=None,
                telegram_file_id=None,
                cache_hit=False,
                uploaded=False,
                missing=True,
            )

        stat = path.stat()
        current_path = str(path)
        current_mtime_ns = int(stat.st_mtime_ns)
        current_size = int(stat.st_size)
        cached = self._storage.get_resume_cache(normalized_name)

        if (
            not force_upload
            and cached is not None
            and cached.file_path == current_path
            and cached.file_mtime_ns == current_mtime_ns
            and cached.file_size == current_size
        ):
            return ResumeDeliveryResult(
                resume_name=normalized_name,
                resume_path=current_path,
                telegram_file_id=cached.telegram_file_id,
                cache_hit=True,
                uploaded=False,
                missing=False,
            )

        uploaded = self._telegram_client.send_document(
            file_path=current_path,
            caption=f"Резюме для отклика: {normalized_name}",
        )
        self._storage.save_resume_cache(
            resume_name=normalized_name,
            file_path=current_path,
            file_mtime_ns=current_mtime_ns,
            file_size=current_size,
            telegram_file_id=uploaded.file_id,
            telegram_file_unique_id=uploaded.file_unique_id,
        )
        return ResumeDeliveryResult(
            resume_name=normalized_name,
            resume_path=current_path,
            telegram_file_id=uploaded.file_id,
            cache_hit=False,
            uploaded=True,
            missing=False,
        )


def _normalize_resume_name(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in KNOWN_RESUME_NAMES:
        raise ValueError(f"Unknown resume identifier: {value}")
    return normalized


def _resolve_resume_path(resumes_dir: Path, resume_name: str) -> Path | None:
    root = resumes_dir.resolve()
    candidate = (resumes_dir / f"{resume_name}.pdf").resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("Resume path traversal is not allowed.") from exc
    if candidate.exists() and candidate.is_file():
        return candidate
    return None
