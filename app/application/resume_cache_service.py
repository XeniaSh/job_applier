from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from app.pdf_validation import InvalidPdfError, validate_pdf_file
from app.resume_policy import LEGACY_RESUME_VARIANT_ALIASES_BY_CANONICAL, normalize_resume_variant_id
from app.resume_profiles import DEFAULT_RESUME_PROFILES_PATH, load_resume_profiles
from app.storage.telegram_delivery import TelegramDeliveryStorage
from app.telegram.client import TelegramClient

KNOWN_RESUME_NAMES: tuple[str, ...] = ("java", "java_ai")


@dataclass(frozen=True)
class ResumeDeliveryResult:
    resume_name: str
    resume_path: str | None
    telegram_file_id: str | None
    cache_hit: bool
    uploaded: bool
    missing: bool
    # Actionable, Telegram-safe reason set only when `missing` is True because
    # the selected variant is unconfigured or its PDF failed structural
    # validation -- names the variant and the PDF's basename only, never a
    # private absolute filesystem path or a raw parser exception.
    warning: str | None = None


class ResumeCacheService:
    def __init__(
        self,
        *,
        resumes_dir: Path,
        storage: TelegramDeliveryStorage,
        telegram_client: TelegramClient,
        resume_paths: Mapping[str, Path] | None = None,
        resume_profiles_path: Path = DEFAULT_RESUME_PROFILES_PATH,
    ) -> None:
        self._resumes_dir = resumes_dir
        self._storage = storage
        self._telegram_client = telegram_client
        # `resume_paths=None` means "no explicit map was supplied" -- load the
        # normal configured `resume_profiles.yaml` mapping. An explicitly
        # supplied map, even an empty one, is authoritative from here on: a
        # variant missing from it must fail closed rather than fall back to a
        # guessed `resumes_dir/<canonical>.pdf` filename (see
        # `_resolve_configured_path`/`get_or_upload`). Config-load failures
        # are deliberately not swallowed here, so they surface visibly
        # instead of silently falling back to guessing.
        if resume_paths is None:
            profiles = load_resume_profiles(resume_profiles_path)
            self._resume_paths = {profile.id: profile.pdf for profile in profiles.profiles}
        else:
            self._resume_paths = dict(resume_paths)

    def get_or_upload(
        self,
        *,
        resume_name: str,
        chat_id: str,
        force_upload: bool = False,
        upload_if_missing: bool = True,
    ) -> ResumeDeliveryResult:
        canonical_name = normalize_resume_variant_id(resume_name)
        if canonical_name is None:
            raise ValueError(f"Unknown resume identifier: {resume_name}")

        configured_path = _resolve_configured_path(self._resume_paths, canonical_name)
        if configured_path is None:
            # The selected variant has no configured path at all -- never
            # guess a `resumes_dir/<canonical>.pdf` filename, even if a valid
            # PDF happens to exist there.
            return ResumeDeliveryResult(
                resume_name=canonical_name,
                resume_path=None,
                telegram_file_id=None,
                cache_hit=False,
                uploaded=False,
                missing=True,
                warning=(
                    f"Resume variant '{canonical_name}' is not configured with a PDF path; "
                    "no cached or uploaded file will be sent."
                ),
            )

        # Deliberate: resolves to the configured canonical variant (or its
        # same-variant legacy alias) only -- never a different variant's
        # file -- and validates it as a real, non-encrypted PDF with at
        # least one page before any cached file_id or upload is used. A
        # missing/unreadable/invalid PDF always fails closed here.
        try:
            validate_pdf_file(configured_path)
        except InvalidPdfError:
            return ResumeDeliveryResult(
                resume_name=canonical_name,
                resume_path=None,
                telegram_file_id=None,
                cache_hit=False,
                uploaded=False,
                missing=True,
                warning=(
                    f"Resume variant '{canonical_name}' PDF is missing or invalid "
                    f"({configured_path.name}); no cached or uploaded file will be sent."
                ),
            )

        path = configured_path.resolve()
        stat = path.stat()
        current_path = str(path)
        current_mtime_ns = int(stat.st_mtime_ns)
        current_size = int(stat.st_size)
        cached = self._storage.get_resume_cache(canonical_name)

        if (
            not force_upload
            and cached is not None
            and cached.file_path == current_path
            and cached.file_mtime_ns == current_mtime_ns
            and cached.file_size == current_size
        ):
            return ResumeDeliveryResult(
                resume_name=canonical_name,
                resume_path=current_path,
                telegram_file_id=cached.telegram_file_id,
                cache_hit=True,
                uploaded=False,
                missing=False,
            )

        if not upload_if_missing:
            # Callback flow disables upload here to avoid duplicate messages.
            return ResumeDeliveryResult(
                resume_name=canonical_name,
                resume_path=current_path,
                telegram_file_id=None,
                cache_hit=False,
                uploaded=False,
                missing=False,
            )

        uploaded = self._telegram_client.send_document(
            file_path=current_path,
            caption=f"Резюме для отклика: {canonical_name}",
        )
        self._storage.save_resume_cache(
            resume_name=canonical_name,
            file_path=current_path,
            file_mtime_ns=current_mtime_ns,
            file_size=current_size,
            telegram_file_id=uploaded.file_id,
            telegram_file_unique_id=uploaded.file_unique_id,
        )
        return ResumeDeliveryResult(
            resume_name=canonical_name,
            resume_path=current_path,
            telegram_file_id=uploaded.file_id,
            cache_hit=False,
            uploaded=True,
            missing=False,
        )


def _resolve_configured_path(resume_paths: Mapping[str, Path], canonical_name: str) -> Path | None:
    """The configured path for `canonical_name`, honoring a configured
    same-variant legacy alias (e.g. `java` for `java_backend`) bidirectionally
    -- see `app.resume_policy` -- but never a different variant's path.
    """
    if canonical_name in resume_paths:
        return resume_paths[canonical_name]
    for legacy_id in LEGACY_RESUME_VARIANT_ALIASES_BY_CANONICAL.get(canonical_name, ()):
        if legacy_id in resume_paths:
            return resume_paths[legacy_id]
    return None
