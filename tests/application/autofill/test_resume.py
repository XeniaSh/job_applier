from __future__ import annotations

from pathlib import Path

import pytest

from app.application.autofill.resolver import ResolvedVacancy
from app.application.autofill.resume import (
    ResumeResolutionError,
    resolve_autofill_resume_selection,
)
from app.collectors.vacancy_collector import NormalizedVacancy
from app.resume_profiles import ResumeProfile, ResumeProfiles
from tests.pdf_fixtures import write_minimal_valid_pdf


def _vacancy(*, title: str = "Backend Engineer", location: str | None = None, description: str = "") -> ResolvedVacancy:
    return ResolvedVacancy(
        source="target_company:greenhouse:agoda",
        external_id="1",
        title=title,
        company="Agoda",
        url="https://job-boards.greenhouse.io/agoda/jobs/1",
        application_url="https://job-boards.greenhouse.io/agoda/jobs/1",
        vacancy=NormalizedVacancy(
            source="target_company:greenhouse:agoda",
            external_id="1",
            title=title,
            company="Agoda",
            location=location,
            employment=None,
            description=description,
            url="https://job-boards.greenhouse.io/agoda/jobs/1",
            published_at=None,
        ),
    )


def _profiles(tmp_path: Path) -> ResumeProfiles:
    return ResumeProfiles(
        tuple(
            ResumeProfile(variant_id, f"{variant_id} description.", write_minimal_valid_pdf(tmp_path / f"{variant_id}.pdf"))
            for variant_id in ("java_backend", "ai_backend", "java_backend_nz", "ai_backend_nz")
        )
    )


def test_resolve_autofill_resume_selection_defaults_to_java_backend(tmp_path: Path) -> None:
    selection = resolve_autofill_resume_selection(
        vacancy=_vacancy(location="Berlin, Germany"), profiles=_profiles(tmp_path)
    )
    assert selection.variant == "java_backend"
    assert selection.path.is_file()
    assert selection.location_warning is None


def test_resolve_autofill_resume_selection_detects_nz_from_normalized_location(tmp_path: Path) -> None:
    selection = resolve_autofill_resume_selection(
        vacancy=_vacancy(location="Auckland, New Zealand"), profiles=_profiles(tmp_path)
    )
    assert selection.variant == "java_backend_nz"


def test_resolve_autofill_resume_selection_ignores_description_only_nz_mention(tmp_path: Path) -> None:
    """Only the structured `location` is used; an employer-office mention in
    the free-text `description` must never select the NZ variant.
    """
    selection = resolve_autofill_resume_selection(
        vacancy=_vacancy(location="Austin, United States", description="We also have an Auckland office."),
        profiles=_profiles(tmp_path),
    )
    assert selection.variant == "java_backend"


def test_resolve_autofill_resume_selection_override_is_validated(tmp_path: Path) -> None:
    selection = resolve_autofill_resume_selection(
        vacancy=_vacancy(),
        profiles=_profiles(tmp_path),
        override_variant="ai_backend_nz",
    )
    assert selection.variant == "ai_backend_nz"


def test_resolve_autofill_resume_selection_rejects_unknown_override(tmp_path: Path) -> None:
    with pytest.raises(ResumeResolutionError):
        resolve_autofill_resume_selection(
            vacancy=_vacancy(),
            profiles=_profiles(tmp_path),
            override_variant="not_a_real_variant",
        )


def test_resolve_autofill_resume_selection_accepts_legacy_override_alias(tmp_path: Path) -> None:
    selection = resolve_autofill_resume_selection(
        vacancy=_vacancy(), profiles=_profiles(tmp_path), override_variant="java_ai"
    )
    assert selection.variant == "ai_backend"


def test_resolve_autofill_resume_selection_fails_closed_on_invalid_pdf(tmp_path: Path) -> None:
    bad_path = tmp_path / "java-backend.pdf"
    bad_path.write_bytes(b"%PDF-1.4\nnot a real pdf body")
    profiles = ResumeProfiles((ResumeProfile("java_backend", "desc", bad_path),))
    with pytest.raises(ResumeResolutionError) as exc_info:
        resolve_autofill_resume_selection(vacancy=_vacancy(), profiles=profiles)
    # Why that variant was selected must survive onto the failure too --
    # never only on a successful `AutofillResumeSelection`.
    assert exc_info.value.reason


def test_resolve_autofill_resume_selection_fails_closed_on_unconfigured_variant(tmp_path: Path) -> None:
    profiles = ResumeProfiles(
        (ResumeProfile("java_backend", "desc", write_minimal_valid_pdf(tmp_path / "java-backend.pdf")),)
    )
    with pytest.raises(ResumeResolutionError) as exc_info:
        resolve_autofill_resume_selection(
            vacancy=_vacancy(location="Auckland, New Zealand"), profiles=profiles
        )
    assert exc_info.value.reason
    assert exc_info.value.location_warning is None
