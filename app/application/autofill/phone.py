from __future__ import annotations

import re
from dataclasses import dataclass

_CALLING_CODE_RE = re.compile(r"\+(\d{1,4})")


def digits_only(value: str) -> str:
    return "".join(char for char in value if char.isdigit())


def extract_calling_code(text: str | None) -> str | None:
    """Return the first +N calling code found in widget UI text, without the plus."""
    if not text:
        return None
    match = _CALLING_CODE_RE.search(text)
    if match:
        return match.group(1)
    stripped = text.strip()
    if stripped.isdigit() and 1 <= len(stripped) <= 4:
        return stripped
    return None


def national_number_for_calling_code(phone: str, calling_code: str | None) -> str:
    """Return the number to type into an intl-tel national input.

    Strips the known country calling code when the stored value is international.
    Does not strip digits when the calling code is unknown or does not prefix the number.
    """
    raw = phone.strip()
    if not raw:
        return raw
    code = digits_only(calling_code or "")
    phone_digits = digits_only(raw)
    if code and phone_digits.startswith(code) and len(phone_digits) > len(code) + 3:
        return phone_digits[len(code) :]
    if raw.startswith("+") or raw.startswith("00"):
        return phone_digits
    return raw


_INTERNATIONAL_PHONE_RE = re.compile(r"^\+[\d\s().-]+$")
_OPTION_CODE_RE = re.compile(r"(?<!\d)\+\s?(\d{1,4})(?!\d)")
_MIN_E164_DIGITS = 8
_MAX_E164_DIGITS = 15
_MIN_NATIONAL_DIGITS = 4


def international_phone_digits(phone: str | None) -> str | None:
    """Digits of a configured `+`-prefixed international phone number, or
    None for a missing, non-international (no leading `+`), malformed, or
    out-of-E.164-length value. Never guesses a country or calling code."""
    raw = (phone or "").strip()
    if not _INTERNATIONAL_PHONE_RE.match(raw):
        return None
    digits = digits_only(raw)
    if not _MIN_E164_DIGITS <= len(digits) <= _MAX_E164_DIGITS:
        return None
    return digits


@dataclass(frozen=True)
class DialOption:
    text: str
    name: str
    code: str


def parse_dial_option(text: str) -> DialOption | None:
    """Split a displayed dialing-code option (e.g. "Uzbekistan +998") into
    its country name and calling code. Options without exactly one `+N`
    token are not dialing-code options and are ignored."""
    cleaned = " ".join((text or "").split())
    tokens = _OPTION_CODE_RE.findall(cleaned)
    if len(tokens) != 1:
        return None
    name = " ".join(_OPTION_CODE_RE.sub(" ", cleaned).split())
    return DialOption(text=cleaned, name=name, code=tokens[0])


# Calling codes known to be shared by several countries/territories. A
# configured number cannot say which one is meant, so these never resolve --
# not even when a filtered/partial/virtualized list renders a single matching
# option. Deliberately a small known set, NOT complete world phone knowledge:
# any other shared code is only caught when the list shows several options.
SHARED_CALLING_CODES = frozenset(
    {"1", "7", "39", "44", "47", "61", "212", "262", "358", "590", "599"}
)


def resolve_dial_option(option_texts: list[str], phone_digits: str) -> DialOption | None:
    """The single displayed option whose calling code prefixes `phone_digits`.

    Fails closed (None) unless exactly one distinct displayed code prefixes
    the number *and* exactly one displayed option carries that code -- so a
    shared code (e.g. US/Canada +1) or overlapping codes (+1 vs +1242) are
    never resolved by arbitrarily picking one. Codes in
    `SHARED_CALLING_CODES` never resolve at all, even with one visible option.
    The code is never inferred from the number alone, only from what the
    option list displays.
    """
    options = [parsed for text in option_texts if (parsed := parse_dial_option(text)) is not None]
    prefixing = {
        option.code
        for option in options
        if phone_digits.startswith(option.code)
        and len(phone_digits) - len(option.code) >= _MIN_NATIONAL_DIGITS
    }
    if len(prefixing) != 1:
        return None
    (code,) = prefixing
    if code in SHARED_CALLING_CODES:
        return None
    carrying = [option for option in options if option.code == code]
    if len(carrying) != 1:
        return None
    return carrying[0]


def calling_code_occurrence_count(composed: str, calling_code: str | None) -> int:
    code = digits_only(calling_code or "")
    if not code:
        codes = _CALLING_CODE_RE.findall(composed)
        if not codes:
            return 0
        code = codes[0]
    token = f"+{code}"
    plus_hits = composed.count(token)
    digits = digits_only(composed)
    prefix_hits = 0
    if digits.startswith(code):
        prefix_hits = 1
        rest = digits[len(code) :]
        if rest.startswith(code):
            prefix_hits = 2
    return max(plus_hits, prefix_hits)


def calling_code_occurs_once(composed: str, calling_code: str | None) -> bool:
    return calling_code_occurrence_count(composed, calling_code) == 1
