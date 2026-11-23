from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.application.autofill.phone import (
    international_phone_digits,
    parse_dial_option,
    resolve_dial_option,
)
from app.application.autofill.questions import QuestionKind
from app.application.autofill.service import _readback_matches

_OPTIONS = [
    "Germany +49",
    "Uzbekistan +998",
    "Canada +1",
    "United States +1",
    "Bahamas +1242",
    "Kazakhstan +7",
    "Select...",
]


@pytest.mark.parametrize(
    ("phone", "digits"),
    [
        ("+998 90 123-45-67", "998901234567"),
        ("+(49) 151 2345 6789", "4915123456789"),
        ("998901234567", None),
        ("00998901234567", None),
        ("+998", None),
        ("+9989012345678901234", None),
        ("+998 90 abc", None),
        (None, None),
    ],
)
def test_international_phone_digits(phone: str | None, digits: str | None) -> None:
    assert international_phone_digits(phone) == digits


def test_parse_dial_option_extracts_name_and_code() -> None:
    parsed = parse_dial_option("🇺🇿 Uzbekistan +998")
    assert parsed is not None
    assert parsed.code == "998"
    assert parsed.name.endswith("Uzbekistan")
    assert parse_dial_option("Select...") is None
    assert parse_dial_option("Weird +1 +2") is None


def test_resolve_uzbekistan_from_full_number() -> None:
    resolved = resolve_dial_option(_OPTIONS, "998901234567")
    assert resolved is not None
    assert (resolved.name, resolved.code) == ("Uzbekistan", "998")
    assert resolved.text == "Uzbekistan +998"


def test_resolve_other_unique_code() -> None:
    resolved = resolve_dial_option(_OPTIONS, "4915123456789")
    assert resolved is not None
    assert resolved.name == "Germany"


def test_shared_plus_one_is_ambiguous() -> None:
    assert resolve_dial_option(_OPTIONS, "15555550100") is None


def test_single_visible_shared_code_never_resolves() -> None:
    # A filtered/partial/virtualized list may render only one country, but the
    # configured number cannot prove US vs Canada, or Russia vs Kazakhstan.
    assert resolve_dial_option(["United States +1"], "15555550100") is None
    assert resolve_dial_option(["Kazakhstan +7"], "77011234567") is None
    assert resolve_dial_option(["Russia +7"], "79161234567") is None


def test_unique_codes_still_resolve_from_partial_list() -> None:
    resolved = resolve_dial_option(["Uzbekistan +998"], "998901234567")
    assert resolved is not None and resolved.name == "Uzbekistan"
    resolved = resolve_dial_option(["Germany +49"], "4915123456789")
    assert resolved is not None and resolved.name == "Germany"


def test_overlapping_codes_are_ambiguous() -> None:
    assert resolve_dial_option(["Canada +1", "Bahamas +1242"], "12425550100") is None


def test_missing_code_in_options_fails_closed() -> None:
    assert resolve_dial_option(["Germany +49", "Select..."], "998901234567") is None
    assert resolve_dial_option([], "998901234567") is None


def test_code_must_leave_a_national_number() -> None:
    assert resolve_dial_option(["Uzbekistan +998"], "998123") is None


@pytest.mark.parametrize(
    ("actual", "expected"),
    [
        ("+998", True),
        ("+1", True),
        ("+49", False),
        ("+998oops", False),
        ("+99 8", False),
        ("+99812345", False),
        ("998", False),
        ("+998 ", True),
    ],
)
def test_dial_country_readback_requires_plain_plus_code(actual: str, expected: bool) -> None:
    item = SimpleNamespace(kind=QuestionKind.PHONE_DIAL_COUNTRY, value="+998901234567")
    if actual == "+1":
        item.value = "+15555550100"
    assert _readback_matches(item, actual) is expected  # type: ignore[arg-type]
