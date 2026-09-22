from __future__ import annotations

import re

from app.application.candidate_profile import (
    REMOTE_ARRANGEMENT_HYBRID_ONSITE,
    REMOTE_ARRANGEMENT_OPEN_ONSITE_OR_LISTED,
    REMOTE_ARRANGEMENT_REMOTE_IN_LISTED_COUNTRIES,
    REMOTE_ARRANGEMENT_REMOTE_OUTSIDE_LISTED_COUNTRIES,
    canonical_academic_level,
    countries_mentioned,
    normalize_country_name,
)

_MAX_CHOICES_RE = re.compile(
    r"(?:select|choose|pick)\s+(?:the\s+)?(?:top|up to|at most)?\s*(\d+)",
    re.IGNORECASE,
)
_RELOCATE_RE = re.compile(
    r"(?:based in|relocate to|relocation to|move to)\s+([A-Za-z][A-Za-z .'-]+?)(?:\s+or\s+|\s*\?|$)",
    re.IGNORECASE,
)
_LOCATED_IN_RE = re.compile(
    r"(?:currently\s+)?(?:located in|reside(?:s)? in|residing in|live in|living in)\s+(.+?)(?:\s*\?|$)",
    re.IGNORECASE,
)
_LOCATED_OR_RELOCATE_RE = re.compile(
    r"(?:currently\s+)?located in\s+(.+?)\s*,?\s*(?:or\s+)?(?:would you\s+)?(?:need|require)(?:\s+to)?\s+relocat",
    re.IGNORECASE,
)
_PLACE_SPLIT_RE = re.compile(r"\s*(?:,|/|\bor\b|\band\b)\s*", re.IGNORECASE)
_RANGE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:-|–|to)\s*(\d+(?:\.\d+)?)", re.IGNORECASE)
_PLUS_RE = re.compile(
    r"(?:more than|over|at least|minimum)?\s*(\d+(?:\.\d+)?)\s*(?:\+|or more|and above)?",
    re.IGNORECASE,
)
_TECH_WORD = r"[A-Za-z0-9+#./&\-]+"
# Separator between words within a captured technology phrase: whitespace, or a
# comma-separated list item (e.g. "Java, Kotlin") — kept intact so the combined
# phrase reaches split_technology_scope_terms instead of being truncated at the
# comma, which would silently drop the additional named technology.
_TECH_WORD_SEP = r"(?:,\s*|\s+)"
# "years (of) experience with/in/using/for <tech>" — tech follows "experience".
_TECH_AFTER_EXPERIENCE_RE = re.compile(
    rf"years?(?:\s+of)?\s+experience\s+(?:with|in|using|for)\s+"
    rf"({_TECH_WORD}(?:{_TECH_WORD_SEP}{_TECH_WORD}){{0,3}})",
    re.IGNORECASE,
)
# "years (of) <tech> experience" — tech precedes "experience".
_TECH_BEFORE_EXPERIENCE_RE = re.compile(
    rf"years?(?:\s+of)?\s+({_TECH_WORD}(?:{_TECH_WORD_SEP}{_TECH_WORD}){{0,2}})\s+experience\b",
    re.IGNORECASE,
)
_TECH_EXPERIENCE_YEARS_RE = re.compile(
    rf"({_TECH_WORD}(?:{_TECH_WORD_SEP}{_TECH_WORD}){{0,2}})\s+experience\s*\(\s*years?\s*\)",
    re.IGNORECASE,
)
# Trailing filler words trimmed off a captured phrase (never a technology name
# by itself): "Spring Boot development" -> "Spring Boot".
_TRAILING_FILLER_WORDS = {"development", "experience", "background", "work"}
# Generic (non-technology) phrases rejected by exact match after trimming, not
# substring match — a technology name that merely contains one of these words
# (e.g. "Spring Boot development" before trimming) must not be rejected outright.
_GENERIC_YEARS_EXPERIENCE_PHRASES = {
    "the industry",
    "this industry",
    "industry",
    "the field",
    "this field",
    "field",
    "the role",
    "this role",
    "role",
    "the position",
    "this position",
    "position",
    "the area",
    "this area",
    "area",
    "the company",
    "this company",
    "company",
    "the team",
    "this team",
    "team",
    "the domain",
    "this domain",
    "domain",
    "software development",
    "development",
    "relevant",
    "overall",
    "total",
    "professional",
    "general",
    "work",
    "hands on",
    "hands-on",
}


def parse_years_experience_technology(text: str) -> str | None:
    """Extract an explicit technology/skill name from a years-of-experience question.

    Returns None for generic overall/relevant/total experience questions (no
    "experience in/with/using/for <X>" or "<X> experience" phrasing, or <X> is
    a generic non-technology phrase such as "the industry"). Never invents or
    hardcodes a specific technology; it only reads what the question itself
    names.
    """
    haystack = text or ""
    match = (
        _TECH_AFTER_EXPERIENCE_RE.search(haystack)
        or _TECH_BEFORE_EXPERIENCE_RE.search(haystack)
        or _TECH_EXPERIENCE_YEARS_RE.search(haystack)
    )
    if not match:
        return None
    words = match.group(1).strip().split()
    while len(words) > 1 and words[-1].lower() in _TRAILING_FILLER_WORDS:
        words = words[:-1]
    technology = " ".join(words)
    if not technology or technology.lower() in _GENERIC_YEARS_EXPERIENCE_PHRASES:
        return None
    return technology


def find_known_technologies_in_text(text: str, known_technologies: list[str]) -> list[str]:
    """All profile-configured technology names present in free text.

    Used to recognize an ambiguous combined years-of-experience question such
    as "Years of experience with Java and Kotlin": when more than one
    configured technology is named, no single technology-specific answer can
    be truthfully inferred. Only ever matches technologies the candidate has
    actually configured; never hardcodes or guesses a technology name.
    """
    haystack = text or ""
    if not haystack:
        return []
    matches: list[str] = []
    seen: set[str] = set()
    for technology in sorted(known_technologies, key=len, reverse=True):
        cleaned = technology.strip()
        if not cleaned or cleaned.lower() in seen:
            continue
        pattern = r"(?<![A-Za-z0-9+#])" + re.escape(cleaned) + r"(?![A-Za-z0-9+#])"
        if re.search(pattern, haystack, re.IGNORECASE):
            seen.add(cleaned.lower())
            matches.append(cleaned)
    return matches


_TECH_SCOPE_SPLIT_RE = re.compile(r"\s*(?:,|/|\band\b|\bor\b)\s*", re.IGNORECASE)


def split_technology_scope_terms(phrase: str | None) -> list[str]:
    """Split a years-of-experience technology phrase on combining words/punctuation.

    Detects a combined/scope expression such as "Java and Kotlin", "Java/Kotlin",
    or "Java, Kotlin" that names more than one target. Used to keep a combined
    question unresolved even when only one of the named terms is a configured
    technology, since a single configured value cannot truthfully answer for an
    unconfigured one. Returns an empty list for an empty phrase, or a
    single-item list when no combinator is present.
    """
    if not phrase or not phrase.strip():
        return []
    return [part.strip() for part in _TECH_SCOPE_SPLIT_RE.split(phrase) if part.strip()]


def find_known_technology_in_text(text: str, known_technologies: list[str]) -> str | None:
    """Token-boundary match of a single profile-configured technology name in free text.

    Used as a conservative fallback when a years-of-experience question
    names a technology without one of the recognized "experience in/with/
    using <X>" or "<X> experience" phrasings (e.g. the technology is named
    elsewhere in the question's surrounding context).
    """
    matches = find_known_technologies_in_text(text, known_technologies)
    return matches[0] if matches else None


def parse_max_choices(text: str) -> int | None:
    match = _MAX_CHOICES_RE.search(text)
    if not match:
        return None
    count = int(match.group(1))
    return count if count > 0 else None


def parse_relocation_destination(text: str) -> str | None:
    found: list[str] = []
    seen: set[str] = set()
    for match in _RELOCATE_RE.finditer(text):
        destination = " ".join(match.group(1).strip().rstrip("?.,").split())
        key = destination.lower()
        if not destination or key in seen:
            continue
        seen.add(key)
        found.append(destination)
    if not found:
        return None
    if len(found) == 1:
        return found[0]
    if all(item.lower() == found[0].lower() for item in found):
        return found[0]
    return found[-1]


def parse_located_in_places(text: str) -> list[str]:
    """Extract named places from a current-residence presence question."""
    match = _LOCATED_IN_RE.search(text or "")
    if not match:
        return []
    blob = match.group(1).strip().rstrip("?.,")
    places: list[str] = []
    seen: set[str] = set()
    for part in _PLACE_SPLIT_RE.split(blob):
        cleaned = _strip_leading_the(part).strip(" ?.,!")
        key = cleaned.lower()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        places.append(cleaned)
    return places


def is_located_or_relocate_choice(text: str) -> bool:
    """True for a combined "are you currently located in <places>, or would
    you need to relocate?" question shape, distinct from a plain located-in
    presence question (no relocate clause) or a plain relocation question
    (no named hiring region to be "already located" in).
    """
    return bool(_LOCATED_OR_RELOCATE_RE.search(text or ""))


def parse_located_or_relocate_places(text: str) -> list[str]:
    """Named hiring-region places from a combined located-in-or-relocate
    question, stopping before the "...or would you need to relocate" clause.

    `parse_located_in_places` cannot be reused here: its regex assumes no
    trailing clause and would otherwise swallow the relocate clause into the
    place list.
    """
    match = _LOCATED_OR_RELOCATE_RE.search(text or "")
    if not match:
        return []
    blob = match.group(1).strip().rstrip("?.,")
    places: list[str] = []
    seen: set[str] = set()
    for part in _PLACE_SPLIT_RE.split(blob):
        cleaned = _strip_leading_the(part).strip(" ?.,!")
        key = cleaned.lower()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        places.append(cleaned)
    return places


ALREADY_LOCATED_CHOICE = "already_located"
WOULD_RELOCATE_CHOICE = "would_relocate"
_LOCATED_OR_RELOCATE_CHOICES = frozenset({ALREADY_LOCATED_CHOICE, WOULD_RELOCATE_CHOICE})


def match_located_or_relocate_option(choice: str | None, options: list[str]) -> str | None:
    """Map a semantic 'already_located' / 'would_relocate' choice to a visible
    option. Never selects a remote-job option: remote availability answers a
    different question than where the candidate lives or would relocate.
    Only returns a match when exactly one non-remote option carries the cue,
    so an ambiguous option set stays unresolved rather than guessing.
    """
    if choice not in _LOCATED_OR_RELOCATE_CHOICES:
        return None
    labels = [item.strip() for item in options if item and item.strip()]
    non_remote = [item for item in labels if "remote" not in item.lower()]
    if choice == ALREADY_LOCATED_CHOICE:
        cues = ("already located", "already in", "already based", "no relocation", "no need to relocate")
    else:
        cues = ("need to relocate", "would relocate", "willing to relocate", "relocate")
    matches = [item for item in non_remote if any(cue in item.lower() for cue in cues)]
    if len(matches) == 1:
        return matches[0]
    return None


def _strip_leading_the(value: str) -> str:
    cleaned = " ".join(value.strip().split())
    if cleaned.lower().startswith("the "):
        return cleaned[4:].strip()
    return cleaned


_CURRENT_LOCATION_SPONSORSHIP_TERMS = (
    "current location",
    "current country",
    "current residence",
    "remain in your current",
    "remain in the current",
    "where you currently live",
    "where you currently reside",
    "in your current location",
)
_VISA_PROGRAM_COUNTRIES = (
    ("highly skilled migrant", "netherlands"),
    ("hsm visa", "netherlands"),
    ("h-1b", "united states"),
    ("h1b", "united states"),
    ("h1-b", "united states"),
    ("tn visa", "united states"),
    ("e-3", "united states"),
    ("green card", "united states"),
    ("skilled worker visa", "united kingdom"),
)


def parse_sponsorship_scope(text: str) -> tuple[str, str | None]:
    """Classify a sponsorship question as current-location, named-country, or generic."""
    haystack = " ".join((text or "").lower().split())
    if any(term in haystack for term in _CURRENT_LOCATION_SPONSORSHIP_TERMS):
        return "current", None
    mentioned = countries_mentioned(haystack)
    if len(mentioned) == 1:
        return "country", mentioned[0]
    if len(mentioned) > 1:
        return "country", mentioned[-1]
    return "generic", None


def option_implied_country(option: str) -> str | None:
    mentioned = countries_mentioned(option)
    if mentioned:
        return mentioned[0]
    lowered = option.lower()
    for term, country in _VISA_PROGRAM_COUNTRIES:
        if term in lowered:
            return country
    return None


_RELOCATION_ALREADY_TERMS = (
    "already relocated",
    "already live",
    "already living",
    "already reside",
    "already residing",
    "already based",
)
_RELOCATION_INTENT_TERMS = (
    "would like to relocate",
    "want to relocate",
    "wish to relocate",
    "willing to relocate",
    "and relocate",
    "and would relocate",
)


def sponsorship_option_relocation_intent(option: str) -> bool | None:
    """Whether a sponsorship option's own text implies future relocation intent
    (True), already-relocated/resident status (False), or neither (None).

    Used only to disambiguate between multiple otherwise-generic "Yes" options
    that differ solely on relocation timing (e.g. "I need a visa and I would
    like to relocate" vs "I need a visa but I have already relocated").
    """
    lowered = option.lower()
    if any(term in lowered for term in _RELOCATION_ALREADY_TERMS):
        return False
    if any(term in lowered for term in _RELOCATION_INTENT_TERMS):
        return True
    return None


def match_sponsorship_option(
    value: bool,
    options: list[str],
    referenced_country: str | None = None,
    *,
    relocation_willing: bool | None = None,
) -> str | None:
    """Map a sponsorship boolean onto a Yes/No option without picking an unrelated visa.

    Country-specific labels such as Netherlands HSM are used only when the
    question's referenced country matches. Generic Yes is preferred. When
    more than one generic "Yes" option remains (e.g. a form distinguishing
    "need a visa and want to relocate" from "need a visa, already relocated"),
    the explicit ``relocation_willing`` fact is used to pick the one that
    matches; if it can't disambiguate, the answer stays unresolved rather than
    guessing.
    """
    labels = [item.strip() for item in options if item and item.strip()]
    if not value:
        return match_yes_no(False, labels) if labels else "No"
    if not labels:
        return "Yes"
    exact = [
        item
        for item in labels
        if item.lower().rstrip(".").strip() == "yes"
    ]
    if exact:
        return exact[0]
    referenced = normalize_country_name(referenced_country) if referenced_country else ""
    safe: list[str] = []
    for item in labels:
        if not re.match(r"^(yes)\b", item.strip().lower()):
            continue
        implied = option_implied_country(item)
        if implied and referenced and implied != referenced:
            continue
        if implied and not referenced:
            continue
        if implied and referenced and implied == referenced:
            safe.append(item)
            continue
        if implied is None:
            safe.append(item)
    if not safe:
        return None
    generic = [item for item in safe if option_implied_country(item) is None]
    candidates = generic if generic else safe
    if len(candidates) == 1:
        return candidates[0]
    if relocation_willing is not None:
        matched = [
            item for item in candidates if sponsorship_option_relocation_intent(item) == relocation_willing
        ]
        if len(matched) == 1:
            return matched[0]
    return None


def label_matches(wanted: str, option: str) -> bool:
    """Match labels without treating Java as JavaScript (identifier-safe contains)."""
    needle = wanted.strip().lower()
    haystack = option.strip().lower()
    if not needle or not haystack:
        return False
    if needle == haystack:
        return True
    if haystack.startswith(needle) and (len(haystack) == len(needle) or not haystack[len(needle)].isalnum()):
        return True
    if needle.startswith(haystack) and (len(needle) == len(haystack) or not needle[len(haystack)].isalnum()):
        return True
    idx = haystack.find(needle)
    if idx < 0:
        return False
    before = haystack[idx - 1] if idx > 0 else " "
    after_index = idx + len(needle)
    after = haystack[after_index] if after_index < len(haystack) else " "
    return not before.isalnum() and not after.isalnum()


def match_option(wanted: str, options: list[str]) -> str | None:
    needle = wanted.strip()
    if not needle or not options:
        return None
    labels = [item.strip() for item in options if item and item.strip()]
    lowered = {item.lower(): item for item in labels}
    exact = lowered.get(needle.lower())
    if exact is not None:
        return exact
    partial = [item for item in labels if label_matches(needle, item)]
    if len(partial) == 1:
        return partial[0]
    return None


def match_skill_option(wanted: str, options: list[str]) -> str | None:
    """Match a technology/skill option. Java does not match JavaScript."""
    return match_option(wanted, options)


def match_yes_no(value: bool, options: list[str]) -> str | None:
    """Map a semantic boolean to a visible Yes/No-style option.

    Prefers labels that start with Yes/No (including "Yes, ..." / "No, ...").
    Does not treat literal true/false as the primary match when Yes/No exists.
    """
    wanted = "Yes" if value else "No"
    labels = [item.strip() for item in options if item and item.strip()]
    if not labels:
        return wanted
    prefixed = [item for item in labels if _yes_no_prefix_match(item, value)]
    if prefixed:
        exact = [item for item in prefixed if item.lower().rstrip(".").strip() in {"yes", "no"}]
        return exact[0] if exact else prefixed[0]
    exact = match_option(wanted, labels)
    if exact is not None:
        return exact
    lowered = {item.lower(): item for item in labels}
    aliases = ("yes", "true", "y") if value else ("no", "false", "n")
    for alias in aliases:
        if alias in lowered:
            return lowered[alias]
    return None


def match_work_authorization_option(value: bool, options: list[str]) -> str | None:
    """Match a visible work-authorization answer without guessing other facts.

    Greenhouse boards sometimes label these choices with the meaning of the
    answer rather than a literal ``Yes``/``No`` prefix (for example, ``I am
    not authorized to work in this country``).  Keep this narrower than the
    general boolean matcher so unrelated attestations are not reinterpreted.
    """
    matched = match_yes_no(value, options)
    if matched is not None:
        return matched
    labels = [item.strip() for item in options if item and item.strip()]
    patterns = (
        (r"\bnot\s+(?:legally\s+)?authorized\b", False),
        (r"\bnot\s+(?:legally\s+)?authorised\b", False),
        (r"\bnot\s+eligible\s+to\s+work\b", False),
        (r"\b(?:legally\s+)?authorized\s+to\s+work\b", True),
        (r"\b(?:legally\s+)?authorised\s+to\s+work\b", True),
        (r"\beligible\s+to\s+work\b", True),
    )
    # A negative label also contains the positive phrase, so remove it from
    # the positive result set before requiring a unique match.
    if not value:
        candidates = [
            label
            for label in labels
            if any(re.search(pattern, label, re.IGNORECASE) for pattern, expected in patterns if not expected)
        ]
    else:
        candidates = [
            label
            for label in labels
            if any(re.search(pattern, label, re.IGNORECASE) for pattern, expected in patterns if expected)
            and not any(re.search(pattern, label, re.IGNORECASE) for pattern, expected in patterns if not expected)
        ]
    return candidates[0] if len(candidates) == 1 else None


PRIVACY_DATA_CUES = (
    "privacy",
    "personal data",
    "data processing",
    "processed in accordance",
)


_AFFIRMATIVE_ACK_LABELS = (
    "acknowledge/confirm",
    "acknowledge",
    "confirm",
    "i acknowledge",
    "i confirm",
    "i agree",
    "i accept",
    "i have read and acknowledge",
)


def match_affirmative_option(value: bool, options: list[str]) -> str | None:
    """Map a semantic yes onto Yes/No or acknowledgement options.

    Privacy/data-transfer questions often offer Acknowledge/Confirm rather than Yes.
    """
    matched = match_yes_no(value, options)
    if matched is not None:
        return matched
    if not value:
        return None
    labels = [item.strip() for item in options if item and item.strip()]
    lowered = {item.lower(): item for item in labels}
    for token in _AFFIRMATIVE_ACK_LABELS:
        if token in lowered:
            return lowered[token]
    for item in labels:
        lowered_item = item.lower()
        if "acknowledge" in lowered_item:
            return item
        if "i understand" in lowered_item and any(
            cue in lowered_item for cue in PRIVACY_DATA_CUES
        ):
            return item
    return None


def _yes_no_prefix_match(option: str, value: bool) -> bool:
    cleaned = option.strip().lower()
    if value:
        return bool(re.match(r"^(yes)\b", cleaned))
    return bool(re.match(r"^(no)\b", cleaned))


_SOURCE_FORBIDDEN_RE = re.compile(
    r"employee\s+referral|\breferral\b|\brecruiter\b|recruitment\s+agency|"
    r"\bagency\b|\bevent\b|\buniversity\b|\bcampus\b|job\s+fair",
    re.IGNORECASE,
)
_SOURCE_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "website",
        (
            "company website",
            "company site",
            "careers website",
            "career website",
            "careers site",
            "career site",
            "careers page",
            "career page",
            "direct application",
            "direct apply",
            "corporate website",
            "company careers",
        ),
    ),
    ("linkedin", ("linkedin",)),
    ("other", ("other",)),
)


def is_forbidden_application_source(option: str) -> bool:
    return bool(_SOURCE_FORBIDDEN_RE.search(option))


def match_application_source(options: list[str], preference: list[str]) -> str | None:
    """Pick a truthful source option. Never selects referral/recruiter/event/university."""
    labels = [item.strip() for item in options if item and item.strip()]
    usable = [item for item in labels if not is_forbidden_application_source(item)]
    if not usable:
        return None
    for wanted in preference:
        match = match_option(wanted, usable)
        if match is not None and not is_forbidden_application_source(match):
            return match
        group = _source_group_for(wanted)
        if group is None:
            continue
        grouped = _options_in_source_group(usable, group)
        if grouped:
            return grouped[0]
    for group_name, _ in _SOURCE_GROUPS:
        grouped = _options_in_source_group(usable, group_name)
        if grouped:
            return grouped[0]
    return None


def _source_group_for(wanted: str) -> str | None:
    needle = wanted.strip().lower()
    for group_name, aliases in _SOURCE_GROUPS:
        if any(alias in needle or needle in alias for alias in aliases):
            return group_name
    return None


def _options_in_source_group(options: list[str], group: str) -> list[str]:
    aliases = next((items for name, items in _SOURCE_GROUPS if name == group), ())
    matches: list[str] = []
    for option in options:
        lowered = option.lower()
        if any(alias in lowered for alias in aliases):
            matches.append(option)
    return matches


def match_years_option(years: float, options: list[str]) -> str | None:
    if not options:
        return str(int(years)) if years == int(years) else str(years)
    for option in options:
        range_match = _RANGE_RE.search(option)
        if range_match:
            low = float(range_match.group(1))
            high = float(range_match.group(2))
            if low <= years <= high:
                return option.strip()
    plus_hits: list[tuple[float, str]] = []
    for option in options:
        cleaned = option.lower()
        if "+" not in cleaned and "or more" not in cleaned and "more than" not in cleaned:
            continue
        plus_match = _PLUS_RE.search(option)
        if plus_match:
            threshold = float(plus_match.group(1))
            if years >= threshold:
                plus_hits.append((threshold, option.strip()))
    if plus_hits:
        plus_hits.sort(key=lambda item: item[0], reverse=True)
        return plus_hits[0][1]
    as_int = str(int(years)) if years == int(years) else str(years)
    numbered = [
        option.strip()
        for option in options
        if re.search(rf"\b{re.escape(as_int)}\b", option)
    ]
    if len(numbered) == 1:
        return numbered[0]
    return match_option(as_int, options)


_ACADEMIC_SPECIALIZATION_MARKER_RE = re.compile(r"[()]|\bof\b|\bin\b", re.IGNORECASE)


def _is_generic_academic_label(option: str) -> bool:
    """True for a plain degree-level label (e.g. "Master's Degree") with no
    named field/specialization or awarded-credential qualifier (e.g. "Master
    of Business Administration (M.B.A.)") that merely shares the same level.
    """
    return not _ACADEMIC_SPECIALIZATION_MARKER_RE.search(option)


def match_academic_option(level: str, options: list[str]) -> str | None:
    """Map an ATS-independent academic token onto a visible form option.

    Does not fall back to a random first option. Diploma is never treated as
    master's because short aliases such as "ma" are whole-word only. When
    several options share the same awarded-degree level (e.g. a generic
    "Master's Degree" alongside a specialized "Master of Business
    Administration (M.B.A.)"), the plain generic label is preferred over a
    specialized/named credential; if that still leaves more than one
    candidate, the match stays unresolved rather than guessing.
    """
    wanted = canonical_academic_level(level)
    labels = [item.strip() for item in options if item and str(item).strip()]
    if not labels:
        return level.strip() or None
    if wanted is None:
        return match_option(level, labels)
    matches = [option for option in labels if canonical_academic_level(option) == wanted]
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]
    generic_matches = [option for option in matches if _is_generic_academic_label(option)]
    if len(generic_matches) == 1:
        return generic_matches[0]
    return None


def select_listed_options(
    profile_values: list[str],
    options: list[str],
    *,
    max_choices: int | None,
) -> list[str]:
    limit = max_choices if max_choices is not None and max_choices > 0 else len(profile_values)
    selected: list[str] = []
    seen: set[str] = set()
    for value in profile_values:
        match = match_skill_option(value, options) if options else value
        if match is None:
            continue
        key = match.lower()
        if key in seen:
            continue
        seen.add(key)
        selected.append(match)
        if len(selected) >= limit:
            break
    return selected


_GENDER_ALIASES: dict[str, tuple[str, ...]] = {
    "female": ("female", "woman", "women", "f"),
    "male": ("male", "man", "men", "m"),
    "non-binary": ("non-binary", "nonbinary", "non binary", "nb", "genderqueer"),
}
_GENDER_DECLINE_RE = re.compile(
    r"prefer not|decline|do not wish|don't wish|undisclosed|not disclose|not to say",
    re.IGNORECASE,
)


def is_declined_gender_option(option: str) -> bool:
    return bool(_GENDER_DECLINE_RE.search(option))


def match_prefer_not_to_disclose_gender(options: list[str]) -> str | None:
    """Pick the visible non-disclosing gender option when the form offers one."""
    labels = [item.strip() for item in options if item and item.strip()]
    declined = [item for item in labels if is_declined_gender_option(item)]
    if not declined:
        return None
    preferred = (
        "prefer not to disclose",
        "prefer not to say",
        "decline to self identify",
        "decline to self-identify",
        "do not wish",
        "don't wish",
        "i don't wish to answer",
        "undisclosed",
    )
    for needle in preferred:
        for option in declined:
            if needle in option.lower():
                return option
    return declined[0]


def match_decline_to_answer_option(options: list[str]) -> str | None:
    """Generic 'I don't wish to answer' style option, not tied to gender
    semantics -- the underlying decline phrasing (prefer not to say / decline
    / do not wish to answer) is the same across demographic questions.
    """
    return match_prefer_not_to_disclose_gender(options)


def match_gender_option(wanted: str, options: list[str]) -> str | None:
    """Map an explicit profile gender to a visible option.

    When the wanted value is a non-disclosing policy answer, prefer that
    visible option. Otherwise match a disclosed identity and skip decline.
    """
    needle = wanted.strip()
    if not needle:
        return None
    if is_declined_gender_option(needle):
        return match_prefer_not_to_disclose_gender(options) or match_option(needle, options)
    labels = [item.strip() for item in options if item and item.strip()]
    usable = [item for item in labels if not is_declined_gender_option(item)]
    if not usable and not labels:
        return needle
    match = match_option(needle, usable)
    if match is not None:
        return match
    canonical = _canonical_gender(needle)
    if canonical is not None:
        aliases = _GENDER_ALIASES[canonical]
        for option in usable:
            lowered = option.lower()
            if any(re.search(rf"\b{re.escape(alias)}\b", lowered) for alias in aliases):
                return option
    return None


def _canonical_gender(value: str) -> str | None:
    cleaned = value.strip().lower()
    if not cleaned:
        return None
    for canonical, aliases in _GENDER_ALIASES.items():
        if any(re.search(rf"\b{re.escape(alias)}\b", cleaned) for alias in aliases):
            return canonical
    return None


def match_interest_option(interests: list[str], options: list[str]) -> str | None:
    if not interests:
        return None
    if not options:
        return interests[0]
    for interest in interests:
        match = match_option(interest, options)
        if match is not None:
            return match
    return None


_REMOTE_ARRANGEMENT_DAY_COUNT_RE = re.compile(r"\d+\s*(?:remote\s*)?days?", re.IGNORECASE)


def classify_remote_work_arrangement_option(option: str) -> str | None:
    """Classify one visible working-arrangement option into a canonical token.

    Generic pattern matching only -- never tied to any specific company's
    exact option wording. Returns None when the option's text doesn't clearly
    fit one of the four categories, so an unrecognized option never gets
    force-matched to the wrong bucket.
    """
    lowered = " ".join(option.lower().split())
    has_onsite = bool(re.search(r"\bon[\s-]?site\b", lowered))
    has_remote = "remote" in lowered
    if has_remote and re.search(r"\boutside\b", lowered):
        return REMOTE_ARRANGEMENT_REMOTE_OUTSIDE_LISTED_COUNTRIES
    if has_onsite and has_remote and _REMOTE_ARRANGEMENT_DAY_COUNT_RE.search(lowered):
        return REMOTE_ARRANGEMENT_HYBRID_ONSITE
    if has_onsite and has_remote and "open" in lowered:
        return REMOTE_ARRANGEMENT_OPEN_ONSITE_OR_LISTED
    if has_onsite and has_remote and "hybrid" in lowered:
        return REMOTE_ARRANGEMENT_HYBRID_ONSITE
    if has_remote and not has_onsite and re.search(r"\bin\b", lowered):
        return REMOTE_ARRANGEMENT_REMOTE_IN_LISTED_COUNTRIES
    return None


def match_remote_work_arrangement_option(preference: str, options: list[str]) -> str | None:
    """Pick the one visible option matching an explicit canonical preference token.

    Fails closed (returns None) unless exactly one option classifies into the
    wanted category -- an unrecognized or ambiguous option set is left
    unresolved rather than guessed.
    """
    labels = [item.strip() for item in options if item and item.strip()]
    matches = [item for item in labels if classify_remote_work_arrangement_option(item) == preference]
    return matches[0] if len(matches) == 1 else None


_SKILL_SET_CUE_RE = re.compile(
    r"\b(?:most\s+proficient|most\s+skilled|most\s+experienced|strongest(?:\s+skill)?|"
    r"primary\s+skill|primarily\s+use|main\s+language|favou?rite\s+language)\b",
    re.IGNORECASE,
)
_SKILL_SET_LIST_RE = re.compile(r":\s*(.+?)\s*\??\s*$")


def parse_named_skill_set(text: str | None) -> list[str] | None:
    """Extract an explicit closed set of named skills/languages/technologies
    from a single-choice proficiency question such as "Which of these
    languages are you most proficient in: Go, Ruby or Python?".

    Requires both a proficiency/primary-skill cue and a colon-introduced
    list; a plain multi-select tech-stack question (no colon-introduced
    closed set) returns None. Never invents a named set -- only reads terms
    the question itself lists.
    """
    haystack = text or ""
    if not _SKILL_SET_CUE_RE.search(haystack):
        return None
    match = _SKILL_SET_LIST_RE.search(haystack)
    if not match:
        return None
    parts = [part.strip(" ?.!") for part in _TECH_SCOPE_SPLIT_RE.split(match.group(1))]
    parts = [part for part in parts if part]
    return parts if len(parts) >= 2 else None


def match_named_skill_set(named_options: list[str], known_technologies: list[str]) -> list[str]:
    """Named options that exactly match one of the candidate's own configured
    technology names (case-insensitive, identifier-exact so "Go" never
    matches "Django" or similar). Returns every match so the caller can
    detect "no match" or "ambiguous" and stay unresolved instead of guessing.
    """
    known_lower = {item.strip().lower() for item in known_technologies if item.strip()}
    return [option for option in named_options if option.strip().lower() in known_lower]
