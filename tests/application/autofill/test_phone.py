from __future__ import annotations

from app.application.autofill.phone import (
    calling_code_occurs_once,
    extract_calling_code,
    national_number_for_calling_code,
)


def test_international_uzbekistan_phone_strips_known_calling_code() -> None:
    assert national_number_for_calling_code("+998901234567", "998") == "901234567"
    assert national_number_for_calling_code("+998 90 123 4567", "998") == "901234567"


def test_national_phone_is_left_intact() -> None:
    assert national_number_for_calling_code("901234567", "998") == "901234567"
    assert national_number_for_calling_code("90 123 4567", "998") == "90 123 4567"


def test_unknown_calling_code_is_not_stripped() -> None:
    assert national_number_for_calling_code("+998901234567", None) == "998901234567"
    assert national_number_for_calling_code("+15555550100", "998") == "15555550100"


def test_composed_value_detects_duplicate_calling_code() -> None:
    assert calling_code_occurs_once("Uzbekistan (+998) 901234567", "998") is True
    assert calling_code_occurs_once("Uzbekistan (+998) +998901234567", "998") is False
    assert calling_code_occurs_once("+998 +998 901234567", "998") is False
    assert extract_calling_code("Change country, selected Uzbekistan (+998)") == "998"
