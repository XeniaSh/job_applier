from __future__ import annotations

import math
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

_COUNTRY_ALIASES = {
    "us": "united states",
    "usa": "united states",
    "united states": "united states",
    "united states of america": "united states",
    "uk": "united kingdom",
    "u.k.": "united kingdom",
    "u.k": "united kingdom",
    "gb": "united kingdom",
    "great britain": "united kingdom",
    "britain": "united kingdom",
    "united kingdom": "united kingdom",
    "de": "germany",
    "germany": "germany",
    "nl": "netherlands",
    "netherlands": "netherlands",
    "th": "thailand",
    "thailand": "thailand",
    "pl": "poland",
    "poland": "poland",
}


def normalize_country_name(value: str) -> str:
    cleaned = " ".join(value.strip().lower().split())
    return _COUNTRY_ALIASES.get(cleaned, cleaned)


def countries_mentioned(text: str) -> list[str]:
    """Canonical country names mentioned in free text. Does not infer from relocation."""
    haystack = " ".join((text or "").lower().split())
    if not haystack:
        return []
    found: list[str] = []
    seen: set[str] = set()
    for alias, canonical in sorted(_COUNTRY_ALIASES.items(), key=lambda item: len(item[0]), reverse=True):
        if not re.search(rf"\b{re.escape(alias)}\b", haystack):
            continue
        if canonical in seen:
            continue
        seen.add(canonical)
        found.append(canonical)
    return found


def _strip_leading_the(value: str) -> str:
    cleaned = " ".join(value.strip().split())
    if cleaned.lower().startswith("the "):
        return cleaned[4:].strip()
    return cleaned


ACADEMIC_HIGH_SCHOOL = "HIGH_SCHOOL"
ACADEMIC_ASSOCIATE = "ASSOCIATE"
ACADEMIC_BACHELOR = "BACHELOR"
ACADEMIC_MASTERS = "MASTERS"
ACADEMIC_DOCTORATE = "DOCTORATE"
ACADEMIC_DIPLOMA = "DIPLOMA"

# ATS-independent awarded-degree tokens. Greenhouse maps these onto visible
# option labels. Short aliases such as "ma" are whole words only so they
# cannot match "Diploma".
_ACADEMIC_LEVEL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        ACADEMIC_DOCTORATE,
        re.compile(
            r"\b(?:ph\.?d|dphil|doctorate|doctoral\s+degree|doctor of(?:\s+philosophy)?|"
            r"candidate of sciences|кандидат(?:а)?\s+наук)\b",
            re.IGNORECASE,
        ),
    ),
    (
        ACADEMIC_MASTERS,
        re.compile(
            r"\b(?:master'?s?|masters|msc|m\.sc|mba|ma|ms|magistr(?:acy|a)?|"
            r"specialist|специалист(?:а|у|ом)?|специалитет)\b",
            re.IGNORECASE,
        ),
    ),
    (
        ACADEMIC_BACHELOR,
        re.compile(
            r"\b(?:bachelor'?s?|bachelors|undergraduate|bs|ba|b\.sc|bsc|бакалавр(?:а|у)?)\b",
            re.IGNORECASE,
        ),
    ),
    (
        ACADEMIC_ASSOCIATE,
        re.compile(r"\bassociate'?s?\b", re.IGNORECASE),
    ),
    (
        ACADEMIC_HIGH_SCHOOL,
        re.compile(r"\b(?:high[\s-]?school|highschool|secondary|ged)\b", re.IGNORECASE),
    ),
    (
        ACADEMIC_DIPLOMA,
        re.compile(r"\bdiploma\b", re.IGNORECASE),
    ),
)
_NEGATED_DOCTORATE_RE = re.compile(
    r"\b(?:no|not|without|w/?o)\s+(?:an?\s+)?(?:awarded\s+)?(?:ph\.?d|doctorate|"
    r"doctoral\s+degree|candidate of sciences|кандидат(?:а)?\s+наук)",
    re.IGNORECASE,
)


def canonical_academic_level(
    value: str | None,
    *,
    ignore_doctorate: bool = False,
) -> str | None:
    """Map free text or an ATS option label onto an awarded-degree token."""
    cleaned = " ".join((value or "").strip().split())
    if not cleaned:
        return None
    skip_doctorate = ignore_doctorate or bool(_NEGATED_DOCTORATE_RE.search(cleaned))
    for level, pattern in _ACADEMIC_LEVEL_PATTERNS:
        if skip_doctorate and level == ACADEMIC_DOCTORATE:
            continue
        if pattern.search(cleaned):
            return level
    return None


def normalize_awarded_academic_level(
    raw: str | None,
    *,
    postgraduate_studies_completed: bool | None = None,
    doctorate_awarded: bool | None = None,
) -> str | None:
    """Highest *awarded* degree for ATS forms.

    Russian specialist / equivalent higher education is master's-level.
    Completed postgraduate / aspirantura study without an awarded doctorate
    does not become Doctorate.
    """
    if doctorate_awarded is True:
        return ACADEMIC_DOCTORATE
    ignore_doctorate = doctorate_awarded is False
    level = canonical_academic_level(raw, ignore_doctorate=ignore_doctorate)
    if level == ACADEMIC_DOCTORATE and doctorate_awarded is not True:
        level = canonical_academic_level(raw, ignore_doctorate=True)
    if level in {
        ACADEMIC_MASTERS,
        ACADEMIC_BACHELOR,
        ACADEMIC_ASSOCIATE,
        ACADEMIC_HIGH_SCHOOL,
    }:
        return level
    if level == ACADEMIC_DOCTORATE:
        return ACADEMIC_DOCTORATE
    if postgraduate_studies_completed is True and doctorate_awarded is not True:
        return ACADEMIC_MASTERS
    if level == ACADEMIC_DIPLOMA:
        return ACADEMIC_DIPLOMA
    return level


class CandidateIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    first_name: str
    last_name: str
    email: str
    phone: str
    current_location: str | None = None
    country: str | None = None

    @field_validator("first_name", "last_name", "email", "phone")
    @classmethod
    def required_identity_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Identity fields must not be empty.")
        return cleaned

    @field_validator("current_location", "country")
    @classmethod
    def optional_stripped_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class ProfessionalLinks(BaseModel):
    model_config = ConfigDict(extra="forbid")

    linkedin: str | None = None
    github: str | None = None
    website: str | None = None
    gitlab_username: str | None = None
    open_source_urls: list[str] = Field(default_factory=list)

    @field_validator("linkedin", "github", "website", "gitlab_username")
    @classmethod
    def optional_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @field_validator("open_source_urls", mode="before")
    @classmethod
    def coerce_open_source_urls(cls, value: object) -> object:
        if value is None:
            return []
        if isinstance(value, str):
            stripped = value.strip()
            return [stripped] if stripped else []
        return value

    @field_validator("open_source_urls")
    @classmethod
    def normalize_open_source_urls(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in value:
            text = " ".join(item.strip().split())
            if not text:
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(text)
        return cleaned


class SalaryExpectations(BaseModel):
    """Optional salary data. Stage 1 does not fill salary unless fill_salary is true."""

    model_config = ConfigDict(extra="forbid")

    amount: int | None = None
    currency: str | None = None
    period: str | None = None
    notes: str | None = None
    fill_salary: bool = False


class TechnologyExperience(BaseModel):
    """Explicit years of experience with one named technology/language/tool."""

    model_config = ConfigDict(extra="forbid")

    technology: str
    years: float

    @field_validator("technology")
    @classmethod
    def required_technology(cls, value: str) -> str:
        cleaned = " ".join(value.strip().split())
        if not cleaned:
            raise ValueError("technology_years technology must not be empty.")
        return cleaned

    @field_validator("years")
    @classmethod
    def finite_nonnegative_years(cls, value: float) -> float:
        if not math.isfinite(value) or value < 0:
            raise ValueError("technology_years years must be a finite, nonnegative number.")
        return value


def _normalize_technology_name(value: str) -> str:
    return " ".join(value.strip().casefold().split())


class Employment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_title: str | None = None
    years_of_experience: float | None = None
    years_of_relevant_experience: float | None = None
    notice_period: str | None = None
    open_to_relocation: bool | None = None
    relocation_destinations: list[str] = Field(default_factory=list)
    highest_academic_level: str | None = None
    postgraduate_studies_completed: bool | None = None
    doctorate_awarded: bool | None = None
    professional_tech_stack: list[str] = Field(default_factory=list)
    preferred_fields_of_interest: list[str] = Field(default_factory=list)
    primary_programming_language: str | None = None
    technology_years: list[TechnologyExperience] = Field(default_factory=list)
    salary_expectations: SalaryExpectations | None = None

    @field_validator("current_title", "notice_period", "highest_academic_level", "primary_programming_language")
    @classmethod
    def optional_stripped_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @field_validator("relocation_destinations", "professional_tech_stack", "preferred_fields_of_interest", mode="before")
    @classmethod
    def coerce_string_list(cls, value: object) -> object:
        if value is None:
            return []
        if isinstance(value, str):
            stripped = value.strip()
            return [stripped] if stripped else []
        return value

    @field_validator("technology_years")
    @classmethod
    def unique_technology_years(cls, value: list[TechnologyExperience]) -> list[TechnologyExperience]:
        seen: set[str] = set()
        for item in value:
            key = _normalize_technology_name(item.technology)
            if key in seen:
                raise ValueError(f"technology_years has a duplicate entry for '{item.technology}'.")
            seen.add(key)
        return value

    @field_validator("relocation_destinations", "professional_tech_stack", "preferred_fields_of_interest")
    @classmethod
    def normalize_string_list(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in value:
            text = " ".join(item.strip().split())
            if not text:
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(text)
        return cleaned

    def awarded_academic_level(self) -> str | None:
        """Highest awarded degree token for ATS forms. Not an ATS option label."""
        return normalize_awarded_academic_level(
            self.highest_academic_level,
            postgraduate_studies_completed=self.postgraduate_studies_completed,
            doctorate_awarded=self.doctorate_awarded,
        )

    def primary_language(self) -> str | None:
        """Strongest explicit professional language. Does not dump the full stack."""
        if self.primary_programming_language:
            return self.primary_programming_language
        return self.professional_tech_stack[0] if self.professional_tech_stack else None

    def technology_years_for(self, technology: str) -> float | None:
        """Explicit configured years for one named technology, or None if unset.

        Never falls back to overall/relevant years_of_experience: a
        technology-specific question with no matching entry must stay
        unresolved rather than inherit unrelated general experience.
        """
        needle = _normalize_technology_name(technology)
        if not needle:
            return None
        for item in self.technology_years:
            if _normalize_technology_name(item.technology) == needle:
                return item.years
        return None

    def known_technology_names(self) -> list[str]:
        """Every technology/language/tool name the candidate has explicitly named.

        Combines technology_years, professional_tech_stack, and
        primary_programming_language (deduplicated). Used to recognize a
        years-of-experience question as scoped to a named skill even when
        it doesn't use a recognized "experience in/with/using <X>" phrasing.
        """
        names: list[str] = []
        seen: set[str] = set()
        for source in (
            [item.technology for item in self.technology_years],
            self.professional_tech_stack,
            [self.primary_programming_language] if self.primary_programming_language else [],
        ):
            for name in source:
                key = _normalize_technology_name(name)
                if not key or key in seen:
                    continue
                seen.add(key)
                names.append(name)
        return names


class CountryWorkAuthorization(BaseModel):
    model_config = ConfigDict(extra="forbid")

    country: str
    authorized: bool | None = None
    requires_sponsorship: bool | None = None

    @field_validator("country")
    @classmethod
    def required_country(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Work authorization country must not be empty.")
        return cleaned


class WorkEligibility(BaseModel):
    model_config = ConfigDict(extra="forbid")

    citizenship: list[str] = Field(default_factory=list)
    work_authorizations: list[CountryWorkAuthorization] = Field(default_factory=list)
    requires_visa_sponsorship: bool | None = None

    @field_validator("citizenship", mode="before")
    @classmethod
    def coerce_citizenship(cls, value: object) -> object:
        if value is None:
            return []
        if isinstance(value, str):
            stripped = value.strip()
            return [stripped] if stripped else []
        return value

    @field_validator("citizenship")
    @classmethod
    def normalize_citizenship(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in value:
            country = " ".join(item.strip().split())
            if not country:
                continue
            key = country.lower()
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(country)
        return cleaned


class ApplicationFiles(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_resume: str
    alternative_resumes: list[str] = Field(default_factory=list)
    cover_letter_strategy: str | None = None

    @field_validator("default_resume")
    @classmethod
    def required_resume_path(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("default_resume must not be empty.")
        return cleaned

    @field_validator("alternative_resumes", mode="before")
    @classmethod
    def coerce_alternative_resumes(cls, value: object) -> object:
        if value is None:
            return []
        if isinstance(value, str):
            stripped = value.strip()
            return [stripped] if stripped else []
        return value


class EmployeeRelationship(BaseModel):
    """Explicit referral / current-employee relationship.

    Unset plus ``application_policy.default_no_undeclared_affiliations``
    answers ordinary relationship questions as No.
    """

    model_config = ConfigDict(extra="forbid")

    has_relationship: bool | None = None
    how_known: str | None = None
    employee_name: str | None = None

    @field_validator("how_known", "employee_name")
    @classmethod
    def optional_stripped_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class ApplicationConsent(BaseModel):
    """Explicit recruiting / privacy consent. Unset means do not answer."""

    model_config = ConfigDict(extra="forbid")

    privacy_data_processing: bool | None = None
    recruiting_contact: bool | None = None


class RelocationPolicy(BaseModel):
    """Generic relocation willingness. Does not imply residence or work authorization."""

    model_config = ConfigDict(extra="forbid")

    willing: bool | None = None


class OfficeWorkPolicy(BaseModel):
    """Willingness to meet a stated office/hybrid attendance requirement.

    This does not imply current residence in that city, local work
    authorization, or that the candidate already works onsite.
    """

    model_config = ConfigDict(extra="forbid")

    willing: bool | None = True


class PrivacyAcknowledgementPolicy(BaseModel):
    """Auto-acknowledge required application/recruitment privacy notices.

    Does not cover marketing, newsletters, SMS, talent-pool retention, or
    unrelated legal declarations.
    """

    model_config = ConfigDict(extra="forbid")

    auto_acknowledge_required: bool = True


class PriorAffiliation(BaseModel):
    """Explicit current or former association with a named organization."""

    model_config = ConfigDict(extra="forbid")

    organization: str
    associated: bool

    @field_validator("organization")
    @classmethod
    def required_organization(cls, value: str) -> str:
        cleaned = " ".join(value.strip().split())
        if not cleaned:
            raise ValueError("Prior affiliation organization must not be empty.")
        return cleaned


DEFAULT_APPLICATION_SOURCE_PREFERENCE = [
    "Company Website",
    "Careers Website",
    "Careers Page",
    "Direct Application",
    "LinkedIn",
    "Other",
]


class QuestionOverride(BaseModel):
    """Explicit reusable answer for a question matched by label text.

    All ``question_contains`` fragments must appear in the question (case-insensitive).
    """

    model_config = ConfigDict(extra="forbid")

    question_contains: list[str]
    answer: bool | str

    @field_validator("question_contains", mode="before")
    @classmethod
    def coerce_contains(cls, value: object) -> object:
        if value is None:
            return []
        if isinstance(value, str):
            stripped = value.strip()
            return [stripped] if stripped else []
        return value

    @field_validator("question_contains")
    @classmethod
    def normalize_contains(cls, value: object) -> list[str]:
        items = [value] if isinstance(value, str) else list(value or [])
        cleaned: list[str] = []
        for item in items:
            text = " ".join(str(item).strip().split())
            if text:
                cleaned.append(text)
        if not cleaned:
            raise ValueError("question_contains must not be empty.")
        return cleaned

    @field_validator("answer", mode="before")
    @classmethod
    def coerce_answer(cls, value: object) -> object:
        if isinstance(value, str):
            cleaned = value.strip()
            lowered = cleaned.lower()
            if lowered in {"yes", "true", "y"}:
                return True
            if lowered in {"no", "false", "n"}:
                return False
            return cleaned
        return value

    def matches(self, question_text: str) -> bool:
        haystack = question_text.lower()
        return all(fragment.lower() in haystack for fragment in self.question_contains)


class ApplicationPolicy(BaseModel):
    """ATS-independent application answers that are not vacancy-specific facts."""

    model_config = ConfigDict(extra="forbid")

    relocation: RelocationPolicy = Field(default_factory=RelocationPolicy)
    office_work: OfficeWorkPolicy = Field(default_factory=OfficeWorkPolicy)
    privacy_acknowledgement: PrivacyAcknowledgementPolicy = Field(
        default_factory=PrivacyAcknowledgementPolicy
    )
    default_no_undeclared_affiliations: bool = True
    has_employment_or_post_employment_restrictions: bool | None = Field(
        default=None,
        description=(
            "Whether the candidate is subject to employment agreements or "
            "post-employment restrictions. Unset means do not answer."
        ),
    )
    prior_affiliations: list[PriorAffiliation] = Field(default_factory=list)
    application_source_preference: list[str] = Field(
        default_factory=lambda: list(DEFAULT_APPLICATION_SOURCE_PREFERENCE)
    )
    newsletter_opt_in: bool = False
    sms_interview_updates: bool = False
    prefer_not_to_disclose_gender: bool = True
    question_overrides: list[QuestionOverride] = Field(default_factory=list)

    @field_validator("prior_affiliations", mode="before")
    @classmethod
    def coerce_prior_affiliations(cls, value: object) -> object:
        if value is None:
            return []
        return value

    @field_validator("application_source_preference", mode="before")
    @classmethod
    def coerce_source_preference(cls, value: object) -> object:
        if value is None:
            return list(DEFAULT_APPLICATION_SOURCE_PREFERENCE)
        if isinstance(value, str):
            stripped = value.strip()
            return [stripped] if stripped else list(DEFAULT_APPLICATION_SOURCE_PREFERENCE)
        return value

    @field_validator("question_overrides", mode="before")
    @classmethod
    def coerce_question_overrides(cls, value: object) -> object:
        if value is None:
            return []
        return value

    @field_validator("application_source_preference")
    @classmethod
    def normalize_source_preference(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in value:
            text = " ".join(item.strip().split())
            if not text:
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(text)
        return cleaned or list(DEFAULT_APPLICATION_SOURCE_PREFERENCE)

    def override_answer_for(self, question_text: str) -> bool | str | None:
        for item in self.question_overrides:
            if item.matches(question_text):
                return item.answer
        return None


class SensitiveVoluntaryData(BaseModel):
    """Demographic answers. Unset by default; Stage 1 must not auto-fill these."""

    model_config = ConfigDict(extra="forbid")

    gender: str | None = None
    ethnicity: str | None = None
    disability: str | None = None
    veteran_status: str | None = None

    @field_validator("gender", "ethnicity", "disability", "veteran_status")
    @classmethod
    def optional_sensitive_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    def is_unset(self) -> bool:
        return (
            self.gender is None
            and self.ethnicity is None
            and self.disability is None
            and self.veteran_status is None
        )


class CandidateProfile(BaseModel):
    """ATS-independent structured candidate profile for autofill.

    Unknown YAML keys are rejected (`extra='forbid'`).
    Location / country never imply work authorization.
    """

    model_config = ConfigDict(extra="forbid")

    identity: CandidateIdentity
    professional_links: ProfessionalLinks = Field(default_factory=ProfessionalLinks)
    employment: Employment = Field(default_factory=Employment)
    work_eligibility: WorkEligibility = Field(default_factory=WorkEligibility)
    application_files: ApplicationFiles
    employee_relationship: EmployeeRelationship = Field(default_factory=EmployeeRelationship)
    application_consent: ApplicationConsent = Field(default_factory=ApplicationConsent)
    application_policy: ApplicationPolicy = Field(default_factory=ApplicationPolicy)
    sensitive: SensitiveVoluntaryData = Field(default_factory=SensitiveVoluntaryData)
    fill_sensitive_fields: bool = False

    def work_authorization_for(self, country: str) -> bool | None:
        """Return explicit authorization for a country, or None if unset.

        Location and citizenship alone do not count as authorization.
        """
        needle = normalize_country_name(country)
        if not needle:
            return None
        for item in self.work_eligibility.work_authorizations:
            if normalize_country_name(item.country) == needle:
                return item.authorized
        return None

    def sponsorship_required_for(self, country: str | None) -> bool | None:
        """Country-specific visa-sponsorship need. Unset means do not answer.

        Does not use relocation willingness, citizenship, or a global
        destination sponsorship flag.
        """
        if not country:
            return None
        needle = normalize_country_name(country)
        if not needle:
            return None
        for item in self.work_eligibility.work_authorizations:
            if normalize_country_name(item.country) != needle:
                continue
            if item.requires_sponsorship is not None:
                return item.requires_sponsorship
        return None

    def sponsorship_answer_for_scope(
        self,
        scope: str,
        named_country: str | None = None,
    ) -> tuple[bool | None, str | None]:
        """Return (answer, referenced_country) for a parsed sponsorship scope.

        ``current`` uses residence country only. ``country`` uses the named
        country. ``generic`` may use the global requires_visa_sponsorship flag.
        """
        if scope == "current":
            country = self.identity.country
            return self.sponsorship_required_for(country), country
        if scope == "country":
            return self.sponsorship_required_for(named_country), named_country
        return self.explicit_requires_visa_sponsorship(), None

    def explicit_requires_visa_sponsorship(self) -> bool | None:
        return self.work_eligibility.requires_visa_sponsorship

    def sensitive_answers_for_autofill(self) -> dict[str, str]:
        """Return sensitive values only when an explicit fill policy is enabled."""
        if not self.fill_sensitive_fields:
            return {}
        answers: dict[str, str] = {}
        for name in ("gender", "ethnicity", "disability", "veteran_status"):
            value = getattr(self.sensitive, name)
            if value is not None:
                answers[name] = value
        return answers

    def may_fill_salary(self) -> bool:
        expectations = self.employment.salary_expectations
        return bool(expectations is not None and expectations.fill_salary)

    def website_for_autofill(self) -> str | None:
        """Return an explicit personal website. GitHub/LinkedIn URLs are not a fallback."""
        url = self.professional_links.website
        if url is None:
            return None
        lowered = url.lower()
        if "github.com" in lowered or "linkedin.com" in lowered:
            return None
        return url

    def awarded_academic_level(self) -> str | None:
        """ATS-independent awarded degree token such as MASTERS or DOCTORATE."""
        return self.employment.awarded_academic_level()

    def primary_programming_language(self) -> str | None:
        return self.employment.primary_language()

    def gitlab_username_for_autofill(self) -> str | None:
        raw = self.professional_links.gitlab_username
        if not raw:
            return None
        cleaned = raw.strip().lstrip("@")
        lowered = cleaned.lower()
        marker = "gitlab.com/"
        if marker in lowered:
            path = cleaned[lowered.index(marker) + len(marker):]
            path = path.split("?", 1)[0].strip("/")
            first = path.split("/", 1)[0].strip()
            return first or None
        return cleaned or None

    def open_source_urls_for_autofill(self) -> list[str]:
        return list(self.professional_links.open_source_urls)

    def relevant_experience_years(self) -> float | None:
        if self.employment.years_of_relevant_experience is not None:
            return self.employment.years_of_relevant_experience
        return self.employment.years_of_experience

    def technology_years_for(self, technology: str) -> float | None:
        """Explicit configured years for one named technology. See Employment."""
        return self.employment.technology_years_for(technology)

    def known_technology_names(self) -> list[str]:
        """Every technology/language/tool name the candidate has explicitly named. See Employment."""
        return self.employment.known_technology_names()

    def relocation_willingness(self) -> bool | None:
        """General willingness to relocate. Does not imply residence or work authorization."""
        if self.application_policy.relocation.willing is not None:
            return self.application_policy.relocation.willing
        return self.employment.open_to_relocation

    def relocation_answer_for(self, destination: str | None = None) -> bool | None:
        """Answer relocation-willingness questions from the generic policy.

        A destination in the question does not require a configured whitelist.
        Current residence is used only when the candidate is actually based there.
        """
        willing = self.relocation_willingness()
        if willing is True:
            return True
        needle = " ".join((destination or "").strip().lower().split())
        if needle:
            location = (self.identity.current_location or "").lower()
            country = (self.identity.country or "").lower()
            if needle in location or needle in country:
                return True
        if willing is False:
            return False
        return None

    def employee_relationship_answer(self) -> bool | None:
        explicit = self.employee_relationship.has_relationship
        if explicit is not None:
            return explicit
        if self.application_policy.default_no_undeclared_affiliations:
            return False
        return None

    def prior_affiliation_answer(self, question_text: str) -> bool | None:
        """Look up an explicit named affiliation, else default No for ordinary affiliation questions."""
        haystack = question_text.lower()
        for item in self.application_policy.prior_affiliations:
            name = item.organization.lower()
            if name and name in haystack:
                return item.associated
        if self.application_policy.default_no_undeclared_affiliations:
            return False
        return None

    def employment_restrictions_answer(self) -> bool | None:
        """Explicit employment-agreement / post-employment-restriction fact.

        Unset means do not answer. This is not a generic legal-compliance default.
        """
        return self.application_policy.has_employment_or_post_employment_restrictions

    def resides_in_any(self, places: list[str]) -> bool | None:
        """Whether current residence matches any named place.

        Uses CandidateProfile country and current_location only. Does not infer
        from relocation willingness, citizenship, or work authorization.
        """
        keys = self._residence_keys()
        if not keys:
            return None
        for place in places:
            wanted = normalize_country_name(_strip_leading_the(place))
            if wanted and wanted in keys:
                return True
        return False

    def _residence_keys(self) -> set[str]:
        keys: set[str] = set()
        if self.identity.country:
            keys.add(normalize_country_name(self.identity.country))
        location = self.identity.current_location or ""
        if location.strip():
            keys.add(normalize_country_name(location))
            for part in re.split(r"[,/;|]", location):
                cleaned = normalize_country_name(_strip_leading_the(part))
                if cleaned:
                    keys.add(cleaned)
        return {item for item in keys if item}

    def application_source_preference(self) -> list[str]:
        return list(self.application_policy.application_source_preference)

    def gender_for_autofill(self) -> str | None:
        """Demographic gender answer for application forms.

        ApplicationPolicy defaults to a non-disclosing option. Profile
        ``sensitive.gender`` is factual data and is not used unless the
        candidate opts out of that policy.
        """
        if self.application_policy.prefer_not_to_disclose_gender:
            return "prefer not to disclose"
        return self.sensitive.gender

    def office_work_answer(self) -> bool | None:
        """Answer office/hybrid attendance questions. Does not change location or work auth."""
        return self.application_policy.office_work.willing

    def may_auto_acknowledge_required_privacy(self) -> bool:
        if self.application_consent.privacy_data_processing is False:
            return False
        return bool(self.application_policy.privacy_acknowledgement.auto_acknowledge_required)

    def privacy_acknowledgement_answer(self, *, required: bool) -> bool | None:
        """Answer a classified application-privacy acknowledgement.

        Required notices may be auto-acknowledged from ApplicationPolicy.
        Optional notices need explicit ``application_consent.privacy_data_processing``.
        """
        explicit = self.application_consent.privacy_data_processing
        if explicit is False:
            return False
        if required and self.may_auto_acknowledge_required_privacy():
            return True
        if explicit is True:
            return True
        return None

    def newsletter_opt_in_answer(self) -> bool:
        return bool(self.application_policy.newsletter_opt_in)

    def sms_interview_updates_answer(self) -> bool:
        return bool(self.application_policy.sms_interview_updates)

    def question_override_answer(self, question_text: str) -> bool | str | None:
        return self.application_policy.override_answer_for(question_text)

