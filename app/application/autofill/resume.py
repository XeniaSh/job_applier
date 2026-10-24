from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.application.autofill.resolver import ResolvedVacancy
from app.resume_policy import ResumePolicyInput, decide_resume_variant, normalize_resume_variant_id
from app.resume_profiles import ResumeProfiles, resolve_configured_resume_variant


class ResumeResolutionError(Exception):
    """Raised when the Stage 1 resume file cannot be used.

    Carries the same typed variant/filename/reason/location-uncertainty
    context a successful `AutofillResumeSelection` would, so a caller can
    still report which variant was selected, why, and surface an actionable
    "supply this variant" warning even on failure -- never just a bare
    message that loses that context.
    """

    def __init__(
        self,
        message: str,
        *,
        variant: str | None = None,
        filename: str | None = None,
        reason: str | None = None,
        location_warning: str | None = None,
    ) -> None:
        super().__init__(message)
        self.variant = variant
        self.filename = filename
        self.reason = reason
        self.location_warning = location_warning


@dataclass(frozen=True)
class AutofillResumeSelection:
    """The resolved, strictly validated resume file for one autofill run,
    plus enough context to report it (and any location uncertainty) back to
    the caller. `path` is always a real, structurally valid PDF -- never a
    legacy candidate-profile default.
    """

    variant: str
    reason: str
    path: Path
    profile_id: str
    location_warning: str | None = None


def _resume_policy_signals_from_vacancy(vacancy: ResolvedVacancy) -> ResumePolicyInput:
    """ATS autofill never runs LLM extraction, so only the normalized
    vacancy's own title/location/description are available -- the policy's
    documented fallback evidence, never full page body text.
    """
    normalized = vacancy.vacancy
    return ResumePolicyInput(
        title=vacancy.title,
        description=normalized.description if normalized is not None else "",
        location=normalized.location if normalized is not None else None,
    )


def resolve_autofill_resume_selection(
    *,
    vacancy: ResolvedVacancy,
    profiles: ResumeProfiles,
    override_variant: str | None = None,
) -> AutofillResumeSelection:
    """Resolve the resume file an ATS adapter must upload for `vacancy`.

    Fails closed with `ResumeResolutionError` (no browser has opened yet at
    the call site) whenever the chosen variant is unknown, unconfigured, or
    its PDF fails structural validation -- never substitutes the default or
    any other variant's file.
    """
    if override_variant is not None:
        variant_id = normalize_resume_variant_id(override_variant)
        if variant_id is None:
            raise ResumeResolutionError(
                f"Unknown resume variant override: {override_variant!r}", variant=override_variant
            )
        reason = f"Resume variant explicitly overridden to '{variant_id}'."
        location_warning = None
    else:
        decision = decide_resume_variant(_resume_policy_signals_from_vacancy(vacancy))
        variant_id = decision.variant.value
        reason = decision.reason
        location_warning = decision.location_warning

    _profile, path, warning = resolve_configured_resume_variant(profiles, variant_id)
    if path is None:
        raise ResumeResolutionError(
            warning or f"Resume variant '{variant_id}' could not be resolved.",
            variant=variant_id,
            filename=_profile.pdf.name if _profile is not None else None,
            reason=reason,
            location_warning=location_warning,
        )

    return AutofillResumeSelection(
        variant=variant_id,
        reason=reason,
        path=path,
        profile_id=_profile.id if _profile is not None else variant_id,
        location_warning=location_warning,
    )
