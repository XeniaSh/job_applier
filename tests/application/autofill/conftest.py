from __future__ import annotations

from pathlib import Path

import pytest

from app.resume_profiles import ResumeProfile, ResumeProfiles
from tests.pdf_fixtures import write_minimal_valid_pdf

# The four canonical variants `app.resume_policy` can select between.
_VARIANT_IDS = ("java_backend", "ai_backend", "java_backend_nz", "ai_backend_nz")


@pytest.fixture(autouse=True)
def default_resume_profiles(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ResumeProfiles:
    """`AutofillService.run`, when constructed without an explicit
    `resume_profiles=` (most tests in this directory), now loads the normal
    configured profiles via `app.resume_profiles.load_resume_profiles` at
    that same seam -- never the legacy, unvalidated candidate-profile
    `default_resume`. Most tests here have no real `resume_profiles.yaml`/
    PDF set up, so this autouse fixture stands in at that seam with four
    synthetic, structurally valid PDFs, keeping the no-injection path
    exercised without a 69-call-site `resume_profiles=` edit everywhere.

    A test that wants to exercise a *missing/invalid* selected PDF on the
    no-injection path can still override this with its own
    `monkeypatch.setattr("app.application.autofill.service.load_resume_profiles", ...)`
    after this fixture has run.
    """
    profiles = ResumeProfiles(
        tuple(
            ResumeProfile(
                variant_id,
                f"{variant_id} description.",
                write_minimal_valid_pdf(tmp_path / f"{variant_id}.pdf"),
            )
            for variant_id in _VARIANT_IDS
        )
    )
    monkeypatch.setattr("app.application.autofill.service.load_resume_profiles", lambda: profiles)
    return profiles
