from pathlib import Path

import pytest

from app.cover_letter_profiles import (
    apply_cover_letter_profile,
    resolve_cover_letter_profile,
)


BASE_LETTER = (
    "I am a Java Backend Engineer with around seven years of experience developing "
    "applications using Java, Spring Boot, REST APIs, and microservices. I have built "
    "and maintained production services, implemented integrations, and improved the "
    "reliability of distributed systems. My experience aligns well with this Senior "
    "Java Developer role because it focuses on the same core technologies and "
    "engineering challenges."
)

NZ_BLOCK_SNIPPET = "Although I currently live outside New Zealand, I am actively seeking relocation opportunities."
NZ_TRAVEL_SNIPPET = "can travel to New Zealand for in-person interviews or meetings if helpful"


@pytest.mark.parametrize(
    ("location", "country"),
    [
        ("Auckland, New Zealand", None),
        ("Wellington", None),
        ("Christchurch", None),
        ("New Zealand", None),
        (None, "New Zealand"),
        ("Auckland", "New Zealand"),
    ],
)
def test_new_zealand_enables_relocation_profile(location: str | None, country: str | None) -> None:
    profile = resolve_cover_letter_profile(location=location, country=country)
    assert profile.name == "relocation"
    assert profile.label == "relocation (New Zealand)"
    rendered = apply_cover_letter_profile(BASE_LETTER, profile)
    assert rendered.startswith(BASE_LETTER)
    assert NZ_BLOCK_SNIPPET in rendered
    assert NZ_TRAVEL_SNIPPET in rendered
    assert "committed to relocating" in rendered


@pytest.mark.parametrize(
    ("location", "country"),
    [
        ("Sydney, Australia", None),
        ("Melbourne, Australia", "Australia"),
        ("Toronto, Canada", None),
        ("Canada", "Canada"),
        ("Remote", None),
        ("United States", None),
        ("San Francisco, USA", "USA"),
        ("Berlin, Germany", None),
        (None, None),
    ],
)
def test_non_nz_locations_use_default_profile(location: str | None, country: str | None) -> None:
    profile = resolve_cover_letter_profile(location=location, country=country)
    assert profile.name == "default"
    assert profile.label == "default"
    rendered = apply_cover_letter_profile(BASE_LETTER, profile)
    assert rendered == BASE_LETTER
    assert "New Zealand" not in rendered
    assert "relocation opportunities" not in rendered


def test_default_cover_letter_body_unchanged_when_relocation_appended() -> None:
    profile = resolve_cover_letter_profile(location="Auckland, New Zealand")
    rendered = apply_cover_letter_profile(BASE_LETTER, profile)
    body, block = rendered.split("\n\n", 1)
    assert body == BASE_LETTER
    assert NZ_BLOCK_SNIPPET in block
    assert "sponsorship" not in block.lower()
    assert "work authorization" not in block.lower()


def test_location_line_from_vacancy_text_enables_relocation() -> None:
    profile = resolve_cover_letter_profile(
        location=None,
        vacancy_text="Title: Java Backend\nLocation: Wellington, New Zealand\nCompany: ACME",
    )
    assert profile.name == "relocation"
    assert profile.label == "relocation (New Zealand)"


def test_relocation_block_file_exists() -> None:
    path = Path("profiles/cover_letter_relocation_new_zealand.md")
    assert path.is_file()
    content = path.read_text(encoding="utf-8")
    assert NZ_BLOCK_SNIPPET in content
    assert NZ_TRAVEL_SNIPPET in content
