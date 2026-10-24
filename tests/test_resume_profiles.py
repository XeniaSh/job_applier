from __future__ import annotations

from pathlib import Path

from app.resume_profiles import (
    DEFAULT_RESUME_PROFILE_ID,
    ResumeProfile,
    ResumeProfiles,
    resolve_configured_resume_variant,
    resolve_validated_resume_path,
)
from tests.pdf_fixtures import write_minimal_valid_pdf


def _profiles(tmp_path: Path) -> tuple[ResumeProfiles, dict[str, Path]]:
    paths = {
        "java_backend": write_minimal_valid_pdf(tmp_path / "java-backend.pdf"),
        "ai_backend": write_minimal_valid_pdf(tmp_path / "ai-backend.pdf"),
        "java_backend_nz": write_minimal_valid_pdf(tmp_path / "java-backend-nz.pdf"),
    }
    profiles = ResumeProfiles(
        tuple(
            ResumeProfile(variant_id, f"{variant_id} description.", path)
            for variant_id, path in paths.items()
        )
    )
    return profiles, paths


def test_default_profile_id_is_java_backend() -> None:
    assert DEFAULT_RESUME_PROFILE_ID == "java_backend"


def test_get_resolves_legacy_java_alias(tmp_path: Path) -> None:
    profiles, paths = _profiles(tmp_path)
    found = profiles.get("java")
    assert found is not None
    assert found.pdf == paths["java_backend"]


def test_get_resolves_legacy_java_ai_alias(tmp_path: Path) -> None:
    profiles, paths = _profiles(tmp_path)
    found = profiles.get("java_ai")
    assert found is not None
    assert found.pdf == paths["ai_backend"]


def test_get_unknown_id_returns_none(tmp_path: Path) -> None:
    profiles, _ = _profiles(tmp_path)
    assert profiles.get("not_a_real_variant") is None


def test_resolve_validated_resume_path_accepts_valid_pdf(tmp_path: Path) -> None:
    profiles, paths = _profiles(tmp_path)
    profile = profiles.get("java_backend")
    assert profile is not None
    path, warning = resolve_validated_resume_path(profile)
    assert warning is None
    assert path == paths["java_backend"].resolve()


def test_resolve_validated_resume_path_rejects_truncated_pdf(tmp_path: Path) -> None:
    bad_path = tmp_path / "bad.pdf"
    bad_path.write_bytes(b"%PDF-1.4\nnot a real pdf body")
    profile = ResumeProfile("ai_backend", "desc", bad_path)
    path, warning = resolve_validated_resume_path(profile)
    assert path is None
    assert warning is not None
    assert "ai_backend" in warning


def test_resolve_validated_resume_path_rejects_missing_file(tmp_path: Path) -> None:
    profile = ResumeProfile("java_backend", "desc", tmp_path / "missing.pdf")
    path, warning = resolve_validated_resume_path(profile)
    assert path is None
    assert warning is not None


def test_resolve_configured_resume_variant_unknown_variant_fails_closed(tmp_path: Path) -> None:
    profiles, _ = _profiles(tmp_path)
    profile, path, warning = resolve_configured_resume_variant(profiles, "ai_backend_nz")
    assert profile is None
    assert path is None
    assert "ai_backend_nz" in warning


def test_resolve_configured_resume_variant_legacy_alias(tmp_path: Path) -> None:
    profiles, paths = _profiles(tmp_path)
    profile, path, warning = resolve_configured_resume_variant(profiles, "java_ai")
    assert warning is None
    assert profile is not None and profile.id == "ai_backend"
    assert path == paths["ai_backend"].resolve()
