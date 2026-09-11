from __future__ import annotations

import re

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
