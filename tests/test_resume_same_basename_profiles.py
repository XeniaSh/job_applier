"""Four resume profiles share one employer-facing basename (the `pdf` path's
basename) and live in separate per-profile directories. Everything here is
synthetic: no personal PDF, no real Telegram, DB with cache only under
`tmp_path`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

from app.application.autofill.resolver import ResolvedVacancy
from app.application.autofill.resume import (
    ResumeResolutionError,
    resolve_autofill_resume_selection,
)
from app.application.resume_cache_service import ResumeCacheService
from app.collectors.vacancy_collector import NormalizedVacancy
from app.resume_profiles import load_resume_profiles
from app.storage.telegram_delivery import TelegramDeliveryStorage
from app.telegram.models import TelegramDocumentRef
from tests.pdf_fixtures import build_labeled_valid_pdf

VARIANT_IDS = ("java_backend", "ai_backend", "java_backend_nz", "ai_backend_nz")
BASENAME = "Synthetic_Person_Resume.pdf"


def _write_config(root: Path, *, create: tuple[str, ...] = VARIANT_IDS) -> Path:
    for variant in create:
        target = root / "resumes" / variant / BASENAME
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(build_labeled_valid_pdf(variant))
    config = root / "resume_profiles.local.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "profiles": [
                    {
                        "id": variant,
                        "description": f"{variant} description.",
                        "pdf": f"resumes/{variant}/{BASENAME}",
                    }
                    for variant in VARIANT_IDS
                ]
            }
        ),
        encoding="utf-8",
    )
    return config


def _vacancy(title: str, description: str = "", location: str | None = None) -> ResolvedVacancy:
    normalized = NormalizedVacancy(
        source="target_company:greenhouse:acme",
        external_id="1",
        title=title,
        company="Acme",
        location=location,
        employment=None,
        description=description,
        url="https://example.test/1",
        published_at=None,
    )
    return ResolvedVacancy(
        source=normalized.source,
        external_id="1",
        title=title,
        company="Acme",
        url=normalized.url,
        application_url=normalized.url,
        vacancy=normalized,
    )


def test_distinct_contents_identical_basename_separate_dirs(tmp_path: Path) -> None:
    config = _write_config(tmp_path)
    profiles = load_resume_profiles(config)

    assert tuple(profile.id for profile in profiles.profiles) == VARIANT_IDS
    assert {profile.pdf.name for profile in profiles.profiles} == {BASENAME}
    assert len({profile.pdf.parent for profile in profiles.profiles}) == 4
    contents = {profile.id: profile.pdf.read_bytes() for profile in profiles.profiles}
    assert len(set(contents.values())) == 4
    for profile in profiles.profiles:
        assert profile.pdf == tmp_path / "resumes" / profile.id / BASENAME


def test_committed_config_uses_placeholder_basename_in_per_profile_dirs() -> None:
    profiles = load_resume_profiles(Path("resume_profiles.yaml"))
    for variant in VARIANT_IDS:
        profile = profiles.get(variant)
        assert profile is not None
        assert profile.pdf == Path("resume_profiles.yaml").parent / "resumes" / variant / (
            "FirstName_LastName_Resume.pdf"
        )


@pytest.mark.parametrize(
    ("vacancy", "expected"),
    [
        (_vacancy("Senior Java Backend Engineer", location="Berlin, Germany"), "java_backend"),
        (_vacancy("Senior Java Backend Engineer", location="Auckland, New Zealand"), "java_backend_nz"),
        (
            _vacancy(
                "Backend Engineer",
                "You will build and integrate LLM-powered automation workflows.",
                "Berlin, Germany",
            ),
            "ai_backend",
        ),
        (
            _vacancy(
                "Backend Engineer",
                "You will build and integrate LLM-powered automation workflows.",
                "Auckland, New Zealand",
            ),
            "ai_backend_nz",
        ),
    ],
)
def test_policy_selection_resolves_selected_profile_full_path(
    tmp_path: Path, vacancy: ResolvedVacancy, expected: str
) -> None:
    profiles = load_resume_profiles(_write_config(tmp_path))

    selection = resolve_autofill_resume_selection(vacancy=vacancy, profiles=profiles)

    assert selection.variant == expected
    assert selection.path == tmp_path / "resumes" / expected / BASENAME
    assert selection.path.read_bytes() == build_labeled_valid_pdf(expected)


@pytest.mark.parametrize("missing", VARIANT_IDS)
def test_missing_selected_profile_file_fails_closed_despite_same_basename_elsewhere(
    tmp_path: Path, missing: str
) -> None:
    present = tuple(variant for variant in VARIANT_IDS if variant != missing)
    profiles = load_resume_profiles(_write_config(tmp_path, create=present))

    with pytest.raises(ResumeResolutionError) as excinfo:
        resolve_autofill_resume_selection(
            vacancy=_vacancy("Any"), profiles=profiles, override_variant=missing
        )

    assert excinfo.value.variant == missing
    assert excinfo.value.filename == BASENAME


@dataclass
class _FakeTelegramClient:
    calls: list[str]

    def send_document(self, *, file_path: str, caption: str) -> TelegramDocumentRef:
        _ = caption
        self.calls.append(file_path)
        return TelegramDocumentRef(
            chat_id="123",
            message_id=100 + len(self.calls),
            file_id=f"FILE_ID_{len(self.calls)}",
            file_unique_id=f"UNIQ_{len(self.calls)}",
        )


def test_telegram_cache_keeps_same_basename_profiles_distinct_and_reuses(tmp_path: Path) -> None:
    config = _write_config(tmp_path)
    client = _FakeTelegramClient(calls=[])
    service = ResumeCacheService(
        resumes_dir=tmp_path / "resumes",
        storage=TelegramDeliveryStorage(db_path=tmp_path / "jobs.db"),
        telegram_client=client,  # type: ignore[arg-type]
        resume_profiles_path=config,
    )

    first = {v: service.get_or_upload(resume_name=v, chat_id="123") for v in VARIANT_IDS}
    again = {v: service.get_or_upload(resume_name=v, chat_id="123") for v in VARIANT_IDS}

    assert all(result.uploaded and not result.missing for result in first.values())
    assert len({result.telegram_file_id for result in first.values()}) == 4
    assert {result.resume_path for result in first.values()} == {
        str((tmp_path / "resumes" / v / BASENAME).resolve()) for v in VARIANT_IDS
    }
    assert len(client.calls) == 4
    for variant in VARIANT_IDS:
        assert again[variant].cache_hit is True
        assert again[variant].uploaded is False
        assert again[variant].telegram_file_id == first[variant].telegram_file_id
    assert len(client.calls) == 4
