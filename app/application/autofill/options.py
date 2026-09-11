from __future__ import annotations

import re

from app.application.candidate_profile import canonical_academic_level

_MAX_CHOICES_RE = re.compile(
    r"(?:select|choose|pick)\s+(?:the\s+)?(?:top|up to|at most)?\s*(\d+)",
    re.IGNORECASE,
)
_RELOCATE_RE = re.compile(
    r"(?:based in|relocate to|relocation to|move to)\s+([A-Za-z][A-Za-z .'-]+?)(?:\s+or\s+|\s*\?|$)",
    re.IGNORECASE,
)
_RANGE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:-|–|to)\s*(\d+(?:\.\d+)?)", re.IGNORECASE)
_PLUS_RE = re.compile(
    r"(?:more than|over|at least|minimum)?\s*(\d+(?:\.\d+)?)\s*(?:\+|or more|and above)?",
    re.IGNORECASE,
)


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


def match_academic_option(level: str, options: list[str]) -> str | None:
    """Map an ATS-independent academic token onto a visible form option.

    Does not fall back to a random first option. Diploma is never treated as
    master's because short aliases such as "ma" are whole-word only.
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
    matches.sort(key=len, reverse=True)
    return matches[0]


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
