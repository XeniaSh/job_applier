"""Shared, deterministic resume-variant policy.

Single source of truth for choosing one of four canonical resume variants --
`java_backend`, `ai_backend`, `java_backend_nz`, `ai_backend_nz` -- from two
independent dimensions:

- AI dimension: does the role involve *substantial, hands-on* AI/LLM
  integration or automation work, not a casual employer AI mention, an
  AI-assisted coding tool, or an optional/negated AI nice-to-have?
- New Zealand dimension: is the position itself actually located in New
  Zealand, decided *only* from the vacancy's own structured location field --
  never from free-form text such as `location_restrictions` or
  `description`, since fuzzy inference over that text cannot safely affirm
  the position's actual country.

Used by both vacancy analysis (`app.vacancy_analyzer`, where LLM-extracted
role/responsibility data is reliable) and ATS autofill (`app.application.
autofill.resume`, where only the normalized title/location/description are
available) so the two paths never duplicate classification rules. Never
makes an LLM call and never affects match/eligibility decisions -- it only
picks a resume file and optionally reports a location uncertainty.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum


class ResumeVariant(StrEnum):
    JAVA_BACKEND = "java_backend"
    AI_BACKEND = "ai_backend"
    JAVA_BACKEND_NZ = "java_backend_nz"
    AI_BACKEND_NZ = "ai_backend_nz"


RESUME_VARIANT_IDS: tuple[str, ...] = tuple(variant.value for variant in ResumeVariant)

# Pre-migration persisted/configured identifiers. Kept as an explicit,
# deliberate mapping (never silently guessed) so old `recommended_resume`
# values and resume_profiles.yaml entries keep resolving to a canonical
# variant after the four-variant migration.
LEGACY_RESUME_VARIANT_ALIASES: dict[str, str] = {
    "java": ResumeVariant.JAVA_BACKEND.value,
    "java_ai": ResumeVariant.AI_BACKEND.value,
    "java-backend": ResumeVariant.JAVA_BACKEND.value,
    "ai-adjacent-backend": ResumeVariant.AI_BACKEND.value,
}


def normalize_resume_variant_id(value: str) -> str | None:
    """Canonical variant id for `value`, honoring legacy aliases, or None."""
    cleaned = (value or "").strip().lower()
    if not cleaned:
        return None
    if cleaned in RESUME_VARIANT_IDS:
        return cleaned
    return LEGACY_RESUME_VARIANT_ALIASES.get(cleaned)


# Reverse of `LEGACY_RESUME_VARIANT_ALIASES`: the legacy ids that map to each
# canonical variant. Lets a lookup for the canonical id ("java_backend")
# also find a still-configured legacy-labeled entry ("java") -- bidirectional
# on purpose, since a pre-migration `resume_profiles.yaml` may keep its old
# ids while the rest of the system has moved to canonical ones.
LEGACY_RESUME_VARIANT_ALIASES_BY_CANONICAL: dict[str, tuple[str, ...]] = {}
for _legacy_id, _canonical_id in LEGACY_RESUME_VARIANT_ALIASES.items():
    LEGACY_RESUME_VARIANT_ALIASES_BY_CANONICAL.setdefault(_canonical_id, ())
    LEGACY_RESUME_VARIANT_ALIASES_BY_CANONICAL[_canonical_id] += (_legacy_id,)
del _legacy_id, _canonical_id


@dataclass(frozen=True)
class ResumePolicyInput:
    """Evidence available for one resume-variant decision.

    `role_type`/`responsibilities`/`mandatory_skills` are the reliable,
    LLM-extracted fields (`app.models.VacancyExtraction`) and are used first
    when non-empty. `optional_skills` is deliberately never treated as
    hands-on AI evidence -- an optional/nice-to-have AI skill must not force
    the AI variant. `title`/`description` are the normalized-vacancy
    fallback used only when the extracted fields are all empty (e.g. ATS
    autofill, which never runs LLM extraction). `location` is the vacancy's
    own structured location (never free body text). `location_restrictions`
    is accepted only for caller compatibility (older callers already pass
    it) and does NOT establish resume geography: fuzzy inference over
    extracted/free-form restriction text cannot safely affirm the position's
    actual country (e.g. "must reside in Australia and employer has offices
    in New Zealand" or "must reside in New Zealand or Australia" cannot be
    told apart by text matching alone), so it is never read by the New
    Zealand decision. When `location` is absent or empty, the decision
    always falls back to the non-New Zealand variant with a warning,
    regardless of what `location_restrictions` (or `description`) says.
    """

    role_type: str = ""
    responsibilities: Sequence[str] = field(default_factory=tuple)
    mandatory_skills: Sequence[str] = field(default_factory=tuple)
    optional_skills: Sequence[str] = field(default_factory=tuple)
    title: str = ""
    description: str = ""
    location: str | None = None
    # Caller-compatibility only -- see class docstring. Never read by
    # `decide_resume_variant`/`_decide_nz`.
    location_restrictions: Sequence[str] = field(default_factory=tuple)


@dataclass(frozen=True)
class ResumeVariantDecision:
    variant: ResumeVariant
    is_ai: bool
    ai_reason: str
    is_nz: bool
    nz_reason: str
    # Set only when the location signal was missing, unparsable, or named
    # New Zealand alongside another location (conflicting/multiple-country
    # location) -- never set for an ordinary non-NZ location.
    location_warning: str | None = None

    @property
    def reason(self) -> str:
        return f"{self.ai_reason} {self.nz_reason}".strip()


# Deliberately specific, hands-on phrases -- never a bare "ai" or "artificial
# intelligence" token -- so a casual employer AI mention ("our AI-powered
# platform"), an AI-assisted coding tool ("GitHub Copilot"), or company
# marketing copy never matches. Multi-word phrases also avoid incidental
# substring hits inside unrelated words (e.g. "retail", "maintainability").
AI_HANDS_ON_MARKERS: tuple[str, ...] = (
    "llm",
    "large language model",
    "genai",
    "generative ai",
    "generative-ai",
    "machine learning",
    "ml engineer",
    "ml pipeline",
    "ml model",
    "nlp",
    "natural language processing",
    "computer vision",
    "ai agent",
    "ai agents",
    "ai automation",
    "ai-powered automation",
    "ai integration",
    "ai-enabled",
    "ai-driven product",
    "rag pipeline",
    "retrieval augmented generation",
    "retrieval-augmented generation",
    "vector database",
    "vector search",
    "langchain",
    "openai api",
    "anthropic api",
    "prompt engineering",
    "fine-tuning",
    "fine tune",
    "model training",
    "conversational ai",
    "chatbot development",
    "artificial intelligence engineer",
)

_NEGATION_WINDOW_CHARS = 28
_NEGATION_WORDS: tuple[str, ...] = ("no ", "not ", "without ", "excluding ", "non-", "never ", "n't ")

# Sentence-local context that disqualifies an otherwise-matching marker: an
# optional/nice-to-have requirement, an AI-assisted *coding tool* mention
# (Copilot etc.), or a bare familiarity/awareness mention -- none of which is
# substantial, hands-on AI/LLM engineering work.
_AI_DISQUALIFYING_CONTEXT: tuple[str, ...] = (
    "nice to have",
    "nice-to-have",
    "optional",
    "preferred but not required",
    "bonus if",
    "a plus",
    "coding assistant",
    "coding assistants",
    "code assistant",
    "code assistants",
    "coding tool",
    "coding tools",
    "copilot",
    "pair programmer",
    "familiarity with",
    "awareness of",
)

# Raw vacancy prose (no reliable LLM extraction) only counts a marker as
# evidence when the same sentence also carries one of these hands-on
# engineering verbs -- so a company-description sentence like "We are an
# LLM-powered company" (no verb) never counts, while "Build LLM
# integrations ..." does.
_HANDS_ON_VERBS: tuple[str, ...] = (
    "build", "built", "building",
    "develop", "developed", "developing",
    "design", "designed", "designing",
    "implement", "implemented", "implementing",
    "create", "created", "creating",
    "integrate", "integrated", "integrating",
    "architect", "architected", "architecting",
    "deploy", "deployed", "deploying",
    "ship", "shipped", "shipping",
    "write", "writing", "wrote",
    "maintain", "maintained", "maintaining",
)

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")
_MARKER_PATTERNS: dict[str, re.Pattern[str]] = {
    # Trailing `s?` so a plural noun phrase ("RAG pipelines", "ML models")
    # still matches a singular marker without opening up to any other
    # substring (word boundaries on both ends keep "fulfillment" etc. safe).
    marker: re.compile(r"\b" + re.escape(marker) + r"s?\b") for marker in AI_HANDS_ON_MARKERS
}
_HANDS_ON_VERB_RE = re.compile(r"\b(" + "|".join(_HANDS_ON_VERBS) + r")\b")

# A hands-on verb attributed to "we"/"our company"/"the team" ("We build an
# LLM-powered platform", "Our company is building an LLM-powered platform")
# describes the employer's product, not the candidate's duties -- unlike an
# imperative duty bullet ("Build LLM integrations ...") or a "you will"
# duty, neither of which has an employer self-reference as the verb's
# subject. Checked in a short window immediately before the verb so an
# unrelated earlier self-reference elsewhere in a long sentence is not
# mistaken for the verb's subject.
_EMPLOYER_SELF_SUBJECT_WINDOW_CHARS = 30
_EMPLOYER_SELF_SUBJECT_RE = re.compile(
    r"\bwe\b|"
    r"\bour (company|team|organization|business)\b|"
    r"\bthe (company|team|organization|business)\b"
)
_CANDIDATE_SUBJECT_RE = re.compile(r"\byou\b")

# A marker inside a "for our <marker>"/"at our <marker>"/"in the <marker>"
# style phrase ("Maintain Java APIs for our LLM-powered platform", "Develop
# Java APIs at our LLM-powered company") names the employer's own product or
# company being described, not the candidate's task object -- unlike a
# marker that is itself the verb's object ("Build LLM integrations ... for
# our backend", where "our" qualifies "backend", not the marker). Any
# preposition directly followed by a determiner ("for an", "at our", "in
# the") immediately before the marker signals this employer/product
# reference, regardless of which specific preposition or determiner is
# used -- a marker anywhere plus any engineering verb in the same sentence
# is not enough on its own.
_EMPLOYER_PRODUCT_PHRASE_WINDOW_CHARS = 20
_EMPLOYER_PRODUCT_PHRASE_RE = re.compile(r"\b(for|at|in|on|within)\s+(our|the|its|their|an?)\b")


def _is_employer_product_description(lowered_sentence: str, marker_start: int) -> bool:
    window_start = max(0, marker_start - _EMPLOYER_PRODUCT_PHRASE_WINDOW_CHARS)
    window = lowered_sentence[window_start:marker_start]
    return bool(_EMPLOYER_PRODUCT_PHRASE_RE.search(window))


def _split_sentences(text: str) -> list[str]:
    return [sentence for sentence in _SENTENCE_SPLIT_RE.split(text) if sentence.strip()]


def _is_negated(haystack: str, match_start: int) -> bool:
    window_start = max(0, match_start - _NEGATION_WINDOW_CHARS)
    window = haystack[window_start:match_start]
    return any(word in window for word in _NEGATION_WORDS)


def _has_disqualifying_context(lowered_sentence: str) -> bool:
    return any(phrase in lowered_sentence for phrase in _AI_DISQUALIFYING_CONTEXT)


def _is_employer_self_description(lowered_sentence: str, verb_start: int) -> bool:
    window_start = max(0, verb_start - _EMPLOYER_SELF_SUBJECT_WINDOW_CHARS)
    window = lowered_sentence[window_start:verb_start]
    return bool(_EMPLOYER_SELF_SUBJECT_RE.search(window)) and not _CANDIDATE_SUBJECT_RE.search(window)


def _has_qualifying_hands_on_verb(lowered_sentence: str) -> bool:
    return any(
        not _is_employer_self_description(lowered_sentence, match.start())
        for match in _HANDS_ON_VERB_RE.finditer(lowered_sentence)
    )


def _find_hands_on_ai_marker(text: str, *, require_hands_on_verb: bool = False) -> str | None:
    """Whole-token AI/LLM marker search, sentence-scoped so negation,
    nice-to-have/optional framing, and AI coding-assistant mentions in one
    sentence never bleed into a genuine match elsewhere. `require_hands_on_verb`
    additionally demands an engineering verb attributed to the candidate (an
    imperative duty bullet or a "you will ..." duty) in the same sentence --
    used only for raw, unextracted vacancy prose where a marker alone (e.g. a
    marketing "AI-powered company" sentence, even one that uses a hands-on verb
    for the employer's own product -- "We build an LLM-powered platform") is
    not reliable evidence of hands-on work.
    """
    if not text.strip():
        return None
    for sentence in _split_sentences(text):
        lowered_sentence = sentence.lower()
        if _has_disqualifying_context(lowered_sentence):
            continue
        if require_hands_on_verb and not _has_qualifying_hands_on_verb(lowered_sentence):
            continue
        for marker, pattern in _MARKER_PATTERNS.items():
            match = pattern.search(lowered_sentence)
            if not match:
                continue
            if _is_negated(lowered_sentence, match.start()):
                continue
            if _is_employer_product_description(lowered_sentence, match.start()):
                continue
            return marker
    return None


def _decide_ai(signals: ResumePolicyInput) -> tuple[bool, str]:
    # Joined with newlines, never spaces: each responsibility/skill is its own
    # sentence-scoped line, so a disqualifying phrase in one entry (e.g. "Use
    # GitHub Copilot coding assistants") can never bleed into a genuine,
    # separate entry (e.g. "Build LLM integrations and automation workflows").
    reliable_evidence = "\n".join(
        [signals.role_type, *signals.responsibilities, *signals.mandatory_skills]
    ).strip()
    marker = _find_hands_on_ai_marker(reliable_evidence)
    if marker:
        return True, f"Hands-on AI/LLM evidence in role or responsibilities ('{marker}')."

    if not reliable_evidence:
        # The title alone (e.g. "Software Engineer, AI Integration") is a
        # direct statement of the role and needs no verb; free-text
        # description prose does, since it may just be marketing copy.
        marker = _find_hands_on_ai_marker(signals.title)
        if marker:
            return True, f"Hands-on AI/LLM evidence in title ('{marker}')."
        marker = _find_hands_on_ai_marker(signals.description, require_hands_on_verb=True)
        if marker:
            return True, f"Hands-on AI/LLM evidence in description ('{marker}')."

    return False, "No substantial hands-on AI/LLM evidence found; defaulting to the Java/Kotlin backend resume."


def force_non_ai(decision: ResumeVariantDecision, *, reason: str) -> ResumeVariantDecision:
    """Explicitly override the AI dimension to False, keeping the NZ dimension.

    Used by callers that already have an independent, stronger reason to
    force the general backend resume (e.g. an incomplete vacancy card with
    no known technology stack) -- never guessed, always an explicit call.
    """
    if not decision.is_ai:
        return decision
    return replace(
        decision,
        is_ai=False,
        ai_reason=reason,
        variant=canonical_variant(is_ai=False, is_nz=decision.is_nz),
    )


_NZ_CITY_WORDS: tuple[str, ...] = (
    "auckland",
    "wellington",
    "christchurch",
    "dunedin",
    "queenstown",
    "tauranga",
)
# Cities whose name alone is not unique to New Zealand (e.g. Hamilton,
# Ontario/Scotland) -- never enough on their own to confirm NZ; only an
# accompanying country mention ("Hamilton, New Zealand") does.
_NZ_AMBIGUOUS_CITY_WORDS: tuple[str, ...] = ("hamilton",)
_NZ_COUNTRY_RE = re.compile(r"\bnew zealand\b|\bnz\b", re.IGNORECASE)
_NZ_CITY_RE = re.compile(r"\b(" + "|".join(_NZ_CITY_WORDS) + r")\b", re.IGNORECASE)
_NZ_AMBIGUOUS_CITY_RE = re.compile(r"\b(" + "|".join(_NZ_AMBIGUOUS_CITY_WORDS) + r")\b", re.IGNORECASE)

# Words that assert a broad/global remote scope -- "Worldwide"/"Global"/
# "Anywhere"/"International" -- as opposed to plain filler ("Remote",
# "Hybrid") that carries no geography signal at all. Both groups are
# stripped out when checking whether a segment names a real "other place",
# but only the broadening group confidently resolves an otherwise-unknown
# segment to non-NZ; plain filler alone leaves geography unknown.
_LOCATION_BROADENING_RE = re.compile(r"\b(worldwide|global|anywhere|international)\b", re.IGNORECASE)
_LOCATION_FILLER_RE = re.compile(
    r"\b(remote|worldwide|global|anywhere|hybrid|onsite|on-site|flexible|"
    r"full-time|part-time|office|headquarters|hq|distributed|international|only|based)\b",
    re.IGNORECASE,
)
# A bare, space-padded hyphen ("NZ - Australia") separates two distinct
# places; a hyphen glued to a word ("on-site", "non-NZ") is not a separator.
_LOCATION_SEPARATOR_RE = re.compile(r"[,/|;()]| and | or | - |\n")


def _location_segments(location: str) -> list[str]:
    return [part.strip() for part in _LOCATION_SEPARATOR_RE.split(location) if part.strip()]


def _segment_nz_country_match(lowered_segment: str) -> re.Match[str] | None:
    for match in _NZ_COUNTRY_RE.finditer(lowered_segment):
        if not _is_negated(lowered_segment, match.start()):
            return match
    return None


class _SegmentKind(StrEnum):
    NZ_CONFIDENT = "nz_confident"
    NZ_AMBIGUOUS = "nz_ambiguous"
    OTHER = "other"
    BROADENING = "broadening"
    FILLER = "filler"


def _classify_segment(segment: str) -> _SegmentKind:
    lowered = segment.lower()
    if _segment_nz_country_match(lowered) is not None or _NZ_CITY_RE.search(lowered):
        return _SegmentKind.NZ_CONFIDENT
    if _NZ_AMBIGUOUS_CITY_RE.search(lowered):
        return _SegmentKind.NZ_AMBIGUOUS
    stripped = _LOCATION_FILLER_RE.sub(" ", lowered)
    letters_only = re.sub(r"[^A-Za-z]", " ", stripped).strip()
    if letters_only:
        return _SegmentKind.OTHER
    if _LOCATION_BROADENING_RE.search(lowered):
        return _SegmentKind.BROADENING
    return _SegmentKind.FILLER


def _decide_nz(location: str | None) -> tuple[bool, str, str | None]:
    # Only the structured `location` field is ever read here -- never
    # `location_restrictions` or `description` -- since fuzzy inference over
    # free-form restriction text cannot safely affirm the position's actual
    # country (see `ResumePolicyInput`'s docstring).
    cleaned_location = (location or "").strip()

    if not cleaned_location:
        return (
            False,
            "No vacancy location is available; defaulting to the non-New Zealand resume variant.",
            "Vacancy location is missing, so the New Zealand resume variant could not be confirmed. "
            "Verify the actual position location manually.",
        )

    segments = _location_segments(cleaned_location)
    if not segments:
        return (
            False,
            "Vacancy location could not be parsed; defaulting to the non-New Zealand resume variant.",
            f"Vacancy location '{cleaned_location}' could not be parsed, so the New Zealand resume "
            "variant could not be confirmed. Verify the actual position location manually.",
        )

    by_kind: dict[_SegmentKind, list[str]] = {kind: [] for kind in _SegmentKind}
    for segment in segments:
        by_kind[_classify_segment(segment)].append(segment)

    nz_confident = by_kind[_SegmentKind.NZ_CONFIDENT]
    nz_ambiguous = by_kind[_SegmentKind.NZ_AMBIGUOUS]
    other = by_kind[_SegmentKind.OTHER]
    broadening = by_kind[_SegmentKind.BROADENING]

    if other:
        if nz_confident:
            return (
                False,
                "Vacancy location names New Zealand alongside another location; defaulting to the "
                "non-New Zealand resume variant.",
                f"Vacancy location '{cleaned_location}' names New Zealand alongside another, conflicting "
                "location. The New Zealand resume variant was not selected. Verify the actual position "
                "location manually.",
            )
        return False, f"Position location does not name New Zealand ('{cleaned_location}').", None

    if nz_confident:
        has_only_qualifier = any("only" in segment.lower() for segment in nz_confident)
        # A broadening word can sit in the *same* segment as the New Zealand
        # mention when there is no separator at all ("Remote worldwide
        # including New Zealand") -- not just in a separate segment ("New
        # Zealand or Remote Worldwide"). Either way it is the same ambiguity.
        broadening_in_nz_segment = any(
            _LOCATION_BROADENING_RE.search(segment.lower()) for segment in nz_confident
        )
        if (broadening or broadening_in_nz_segment) and not has_only_qualifier:
            return (
                False,
                "Vacancy location names New Zealand alongside a broader remote/worldwide scope with no "
                "explicit restriction; defaulting to the non-New Zealand resume variant.",
                f"Vacancy location '{cleaned_location}' is ambiguous: it names New Zealand alongside a "
                "broader remote/worldwide scope with no explicit 'only' restriction to New Zealand. "
                "Verify the actual position location manually.",
            )
        return True, f"Position location names New Zealand explicitly ('{cleaned_location}').", None

    if nz_ambiguous:
        return (
            False,
            "Vacancy location names a city that is not unique to New Zealand, with no confirming "
            "country; defaulting to the non-New Zealand resume variant.",
            f"Vacancy location '{cleaned_location}' names a city that is not unique to New Zealand "
            "(e.g. Hamilton), with no country to confirm it. Verify the actual position location "
            "manually.",
        )

    if broadening:
        return False, f"Position location does not name New Zealand ('{cleaned_location}').", None

    return (
        False,
        "Vacancy location carries no identifiable geography; defaulting to the non-New Zealand resume "
        "variant.",
        f"Vacancy location '{cleaned_location}' carries no identifiable geography, so the New Zealand "
        "resume variant could not be confirmed. Verify the actual position location manually.",
    )


def canonical_variant(*, is_ai: bool, is_nz: bool) -> ResumeVariant:
    """The one canonical-variant mapping table, used internally by
    `force_non_ai` and `decide_resume_variant` so the AI x New Zealand
    combination is never duplicated elsewhere.
    """
    if is_ai and is_nz:
        return ResumeVariant.AI_BACKEND_NZ
    if is_ai:
        return ResumeVariant.AI_BACKEND
    if is_nz:
        return ResumeVariant.JAVA_BACKEND_NZ
    return ResumeVariant.JAVA_BACKEND


def decide_resume_variant(signals: ResumePolicyInput) -> ResumeVariantDecision:
    """Decide the canonical resume variant for one vacancy. Pure, deterministic,
    makes no LLM call, and never raises on malformed/empty input.
    """
    is_ai, ai_reason = _decide_ai(signals)
    # `signals.location_restrictions` is deliberately never read here -- see
    # `ResumePolicyInput`'s docstring.
    is_nz, nz_reason, location_warning = _decide_nz(signals.location)
    variant = canonical_variant(is_ai=is_ai, is_nz=is_nz)
    return ResumeVariantDecision(
        variant=variant,
        is_ai=is_ai,
        ai_reason=ai_reason,
        is_nz=is_nz,
        nz_reason=nz_reason,
        location_warning=location_warning,
    )
