from pathlib import Path

import pytest

from app.company_watch.config_loader import (
    DEFAULT_TARGET_COMPANIES_PATH,
    TargetCompaniesConfigLoadError,
    load_target_companies_config,
)

MINIMAL_VALID_YAML = """
companies:
  - name: Acme
    priority: A
    language: english
    relocation_status: remote_global
    watcher_type: greenhouse
"""


def test_load_minimal_valid_yaml(tmp_path: Path) -> None:
    config_file = tmp_path / "target_companies.yaml"
    config_file.write_text(MINIMAL_VALID_YAML, encoding="utf-8")

    config = load_target_companies_config(config_file)

    assert len(config.companies) == 1
    company = config.companies[0]
    assert company.name == "Acme"
    assert company.priority == "A"
    assert company.language == "english"
    assert company.relocation_status == "remote_global"
    assert company.watcher_type == "greenhouse"
    assert company.known_hiring_locations == []
    assert company.role_keywords == []
    assert company.role_title_keywords == []
    assert company.role_description_keywords == []
    assert company.exclude_title_keywords == []
    assert company.notes == []
    assert company.career_url is None
    assert company.job_board_url is None
    assert company.ats is None


def test_load_real_target_companies_yaml() -> None:
    config = load_target_companies_config(DEFAULT_TARGET_COMPANIES_PATH)

    assert config.companies
    names = [company.name for company in config.companies]
    assert "Agoda" in names
    agoda = next(company for company in config.companies if company.name == "Agoda")
    assert agoda.priority == "A"
    assert agoda.watcher_type == "greenhouse"
    assert agoda.known_hiring_locations
    assert agoda.role_keywords
    assert agoda.role_title_keywords
    assert agoda.career_url is not None
    assert agoda.job_board_url is not None

    vinted = next(company for company in config.companies if company.name == "Vinted")
    exness = next(company for company in config.companies if company.name == "Exness")
    assert vinted.watcher_type == "manual"
    assert vinted.ats == "custom"
    assert exness.watcher_type == "manual"
    assert exness.ats == "custom"

    wolt = next(company for company in config.companies if company.name == "Wolt")
    assert wolt.watcher_type == "greenhouse"
    assert wolt.ats == "greenhouse"
    assert wolt.job_board_url == "https://job-boards.greenhouse.io/wolt"
    assert wolt.role_title_keywords == [
        "backend",
        "back-end",
        "java",
        "jvm",
        "kotlin",
        "server",
        "platform engineer",
        "software engineer",
    ]


_STANDARD_ROLE_TITLE_KEYWORDS = [
    "backend",
    "back-end",
    "java",
    "jvm",
    "kotlin",
    "server",
    "platform engineer",
    "software engineer",
]


def test_ashby_boards_cover_verified_companies_with_unique_slugs() -> None:
    config = load_target_companies_config(DEFAULT_TARGET_COMPANIES_PATH)

    ashby_companies = [company for company in config.companies if company.watcher_type == "ashby"]
    assert len(ashby_companies) == 14

    expected_name_to_slug = {
        "TravelPerk / Perk": "Perk",
        "ClickHouse": "clickhouse",
        "Supabase": "supabase",
        "Constructor": "constructor",
        "Kestra": "kestra",
        "Trigger.dev": "triggerdev",
        "Moss": "moss",
        "Sentry": "sentry",
        "Kayak": "kayak",
        "Coder": "coder",
        "incident.io": "incident",
        "Taktile": "taktile",
        "n8n": "n8n",
        "Lovable": "lovable",
    }
    assert {company.name for company in ashby_companies} == set(expected_name_to_slug)

    slugs = [
        company.job_board_url.rsplit("/", maxsplit=1)[-1]
        for company in ashby_companies
        if company.job_board_url is not None
    ]
    assert len(slugs) == len(set(slugs)), "Ashby board slugs must be unique"

    for company in ashby_companies:
        expected_slug = expected_name_to_slug[company.name]
        assert company.ats == "ashby"
        assert company.job_board_url == f"https://jobs.ashbyhq.com/{expected_slug}"

    # TravelPerk / Perk is a pre-existing, hand-verified entry with its own
    # confirmed_role_based/legacy role_keywords metadata; only the thirteen
    # newly added Ashby companies use the shared mixed_check_per_role/
    # remote_global + standard role_title_keywords metadata.
    newly_added_ashby_companies = [
        company for company in ashby_companies if company.name != "TravelPerk / Perk"
    ]
    assert len(newly_added_ashby_companies) == 13
    for company in newly_added_ashby_companies:
        assert company.relocation_status in {"mixed_check_per_role", "remote_global"}
        assert company.role_title_keywords == _STANDARD_ROLE_TITLE_KEYWORDS


def test_missing_file_raises_clear_error(tmp_path: Path) -> None:
    missing_file = tmp_path / "missing.yaml"

    with pytest.raises(TargetCompaniesConfigLoadError, match="not found") as exc_info:
        load_target_companies_config(missing_file)

    assert str(missing_file) in str(exc_info.value)


@pytest.mark.parametrize(
    ("content", "match"),
    [
        ("", "empty"),
        ("companies: not-a-list\n", "invalid"),
        ("- just a list\n", "must be a mapping"),
        ("other_key: []\n", "invalid"),
    ],
)
def test_invalid_structure_raises_clear_error(
    tmp_path: Path,
    content: str,
    match: str,
) -> None:
    config_file = tmp_path / "target_companies.yaml"
    config_file.write_text(content, encoding="utf-8")

    with pytest.raises(TargetCompaniesConfigLoadError, match=match):
        load_target_companies_config(config_file)


@pytest.mark.parametrize(
    ("blank_field", "content"),
    [
        (
            "name",
            "companies:\n  - name: ' '\n    priority: A\n    language: english\n"
            "    relocation_status: remote_global\n    watcher_type: greenhouse\n",
        ),
        (
            "priority",
            "companies:\n  - name: Acme\n    priority: ' '\n    language: english\n"
            "    relocation_status: remote_global\n    watcher_type: greenhouse\n",
        ),
        (
            "watcher_type",
            "companies:\n  - name: Acme\n    priority: A\n    language: english\n"
            "    relocation_status: remote_global\n    watcher_type: ' '\n",
        ),
    ],
)
def test_blank_required_fields_are_rejected(
    tmp_path: Path,
    blank_field: str,
    content: str,
) -> None:
    config_file = tmp_path / "target_companies.yaml"
    config_file.write_text(content, encoding="utf-8")

    with pytest.raises(TargetCompaniesConfigLoadError, match=blank_field):
        load_target_companies_config(config_file)


def test_load_real_target_companies_yaml_merges_greenhouse_catalog() -> None:
    config = load_target_companies_config(DEFAULT_TARGET_COMPANIES_PATH)

    names = [company.name for company in config.companies]
    # Hand-maintained companies are preserved untouched.
    assert "Agoda" in names
    agoda = next(company for company in config.companies if company.name == "Agoda")
    assert agoda.known_hiring_locations
    assert agoda.job_board_url == "https://job-boards.greenhouse.io/agoda"

    # Catalog entries are merged in with generic Greenhouse defaults.
    stripe = next(company for company in config.companies if company.name == "Stripe")
    assert stripe.priority == "A"
    assert stripe.language == "english"
    assert stripe.relocation_status == "mixed_check_per_role"
    assert stripe.watcher_type == "greenhouse"
    assert stripe.ats == "greenhouse"
    assert stripe.job_board_url == "https://job-boards.greenhouse.io/stripe"
    assert "backend" in stripe.role_title_keywords
    assert "java" in stripe.role_title_keywords
    assert stripe.known_hiring_locations == []

    # The hand-maintained MongoDB entry takes precedence over the catalog's
    # same-named/slugged entry: no second, catalog-flavored "MongoDB" appears.
    mongo_matches = [company for company in config.companies if company.name == "MongoDB"]
    assert len(mongo_matches) == 1
    assert mongo_matches[0].watcher_type == "custom"


def test_custom_config_without_sibling_catalog_loads_only_declared_companies(tmp_path: Path) -> None:
    config_file = tmp_path / "target_companies.yaml"
    config_file.write_text(MINIMAL_VALID_YAML, encoding="utf-8")

    config = load_target_companies_config(config_file)

    assert len(config.companies) == 1
    assert config.companies[0].name == "Acme"


def test_custom_config_with_sibling_catalog_merges_catalog_entries(tmp_path: Path) -> None:
    config_file = tmp_path / "target_companies.yaml"
    config_file.write_text(MINIMAL_VALID_YAML, encoding="utf-8")
    catalog_file = tmp_path / "greenhouse_target_boards.yaml"
    catalog_file.write_text(
        "boards:\n  - name: Stripe\n    slug: stripe\n  - name: Figma\n    slug: figma\n",
        encoding="utf-8",
    )

    config = load_target_companies_config(config_file)

    names = {company.name for company in config.companies}
    assert names == {"Acme", "Stripe", "Figma"}
    stripe = next(company for company in config.companies if company.name == "Stripe")
    assert stripe.watcher_type == "greenhouse"
    assert stripe.job_board_url == "https://job-boards.greenhouse.io/stripe"


def test_catalog_entry_explicit_override_takes_precedence(tmp_path: Path) -> None:
    config_file = tmp_path / "target_companies.yaml"
    config_file.write_text(
        "companies:\n"
        "  - name: Stripe\n"
        "    priority: A\n"
        "    language: english\n"
        "    relocation_status: confirmed_role_based\n"
        "    watcher_type: greenhouse\n"
        "    ats: greenhouse\n"
        "    job_board_url: https://job-boards.greenhouse.io/stripe\n"
        "    known_hiring_locations: [Dublin, Ireland]\n",
        encoding="utf-8",
    )
    catalog_file = tmp_path / "greenhouse_target_boards.yaml"
    catalog_file.write_text(
        "boards:\n  - name: Stripe\n    slug: stripe\n",
        encoding="utf-8",
    )

    config = load_target_companies_config(config_file)

    stripe_matches = [company for company in config.companies if company.name == "Stripe"]
    assert len(stripe_matches) == 1
    assert stripe_matches[0].relocation_status == "confirmed_role_based"
    assert stripe_matches[0].known_hiring_locations == ["Dublin", "Ireland"]


def test_catalog_duplicate_slug_raises_clear_error(tmp_path: Path) -> None:
    config_file = tmp_path / "target_companies.yaml"
    config_file.write_text(MINIMAL_VALID_YAML, encoding="utf-8")
    catalog_file = tmp_path / "greenhouse_target_boards.yaml"
    catalog_file.write_text(
        "boards:\n"
        "  - name: Stripe\n"
        "    slug: stripe\n"
        "  - name: Stripe Inc\n"
        "    slug: stripe\n",
        encoding="utf-8",
    )

    with pytest.raises(TargetCompaniesConfigLoadError, match="duplicate"):
        load_target_companies_config(config_file)


def test_catalog_duplicate_name_raises_clear_error(tmp_path: Path) -> None:
    config_file = tmp_path / "target_companies.yaml"
    config_file.write_text(MINIMAL_VALID_YAML, encoding="utf-8")
    catalog_file = tmp_path / "greenhouse_target_boards.yaml"
    catalog_file.write_text(
        "boards:\n"
        "  - name: Stripe\n"
        "    slug: stripe\n"
        "  - name: Stripe\n"
        "    slug: stripe-2\n",
        encoding="utf-8",
    )

    with pytest.raises(TargetCompaniesConfigLoadError, match="duplicate"):
        load_target_companies_config(config_file)


def test_catalog_identical_duplicate_entry_raises_clear_error(tmp_path: Path) -> None:
    config_file = tmp_path / "target_companies.yaml"
    config_file.write_text(MINIMAL_VALID_YAML, encoding="utf-8")
    catalog_file = tmp_path / "greenhouse_target_boards.yaml"
    catalog_file.write_text(
        "boards:\n"
        "  - name: Stripe\n"
        "    slug: stripe\n"
        "  - name: Stripe\n"
        "    slug: stripe\n",
        encoding="utf-8",
    )

    with pytest.raises(TargetCompaniesConfigLoadError, match="duplicate"):
        load_target_companies_config(config_file)


@pytest.mark.parametrize(
    ("content", "match"),
    [
        ("", "empty"),
        ("boards: not-a-list\n", "invalid"),
        ("- just a list\n", "must be a mapping"),
        ("other_key: []\n", "invalid"),
        ("boards:\n  - name: Stripe\n", "invalid"),
        ("boards:\n  - slug: stripe\n", "invalid"),
        ("boards:\n  - name: ' '\n    slug: stripe\n", "invalid"),
    ],
)
def test_malformed_catalog_raises_clear_error(
    tmp_path: Path,
    content: str,
    match: str,
) -> None:
    config_file = tmp_path / "target_companies.yaml"
    config_file.write_text(MINIMAL_VALID_YAML, encoding="utf-8")
    catalog_file = tmp_path / "greenhouse_target_boards.yaml"
    catalog_file.write_text(content, encoding="utf-8")

    with pytest.raises(TargetCompaniesConfigLoadError, match=match):
        load_target_companies_config(config_file)


def test_extra_field_is_rejected(tmp_path: Path) -> None:
    config_file = tmp_path / "target_companies.yaml"
    config_file.write_text(
        "\n".join(
            [
                "companies:",
                "  - name: Acme",
                "    priority: A",
                "    language: english",
                "    relocation_status: remote_global",
                "    watcher_type: greenhouse",
                "    unexpected_typo: true",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(TargetCompaniesConfigLoadError, match="unexpected_typo"):
        load_target_companies_config(config_file)
