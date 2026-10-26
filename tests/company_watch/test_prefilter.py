from app.collectors.vacancy_collector import NormalizedVacancy
from app.company_watch.config_loader import DEFAULT_TARGET_COMPANIES_PATH, load_target_companies_config
from app.company_watch.models import TargetCompany
from app.company_watch.prefilter import passes_role_prefilter


def _company(**overrides: object) -> TargetCompany:
    payload: dict[str, object] = {
        "name": "Agoda",
        "priority": "A",
        "language": "english",
        "relocation_status": "confirmed_role_based",
        "watcher_type": "greenhouse",
    }
    payload.update(overrides)
    return TargetCompany.model_validate(payload)


def _vacancy(*, title: str, description: str = "") -> NormalizedVacancy:
    return NormalizedVacancy(
        source="target_company:greenhouse:agoda",
        external_id="1",
        title=title,
        company="Agoda",
        location="Bangkok",
        employment=None,
        description=description,
        url="https://job-boards.greenhouse.io/agoda/jobs/1",
        published_at=None,
    )


def test_title_keyword_match_passes() -> None:
    company = _company(role_title_keywords=["java", "backend"])
    vacancy = _vacancy(title="Senior Java Engineer", description="Sales operations")
    assert passes_role_prefilter(vacancy, company) is True


def test_description_only_match_does_not_pass() -> None:
    company = _company(role_keywords=["java", "backend"])
    vacancy = _vacancy(title="Warehouse Operator", description="Java backend services")
    assert passes_role_prefilter(vacancy, company) is False


def test_exclude_title_keywords_win_over_include() -> None:
    company = _company(
        role_title_keywords=["software engineer", "backend"],
        exclude_title_keywords=["staff"],
    )
    vacancy = _vacancy(title="Staff Software Engineer")
    assert passes_role_prefilter(vacancy, company) is False


def test_empty_include_keywords_do_not_filter() -> None:
    company = _company(role_keywords=[], role_title_keywords=[])
    other = _vacancy(title="Warehouse Operator")
    backend = _vacancy(title="Java Backend Engineer")
    assert passes_role_prefilter(other, company) is True
    assert passes_role_prefilter(backend, company) is True


def test_legacy_role_keywords_match_title_only() -> None:
    company = _company(role_keywords=["java", "backend"])
    assert passes_role_prefilter(_vacancy(title="Backend Engineer"), company) is True
    assert passes_role_prefilter(
        _vacancy(title="Warehouse Operator", description="We use Java and backend services"),
        company,
    ) is False


def test_role_title_keywords_take_precedence_over_role_keywords() -> None:
    company = _company(
        role_keywords=["java"],
        role_title_keywords=["kotlin"],
    )
    assert passes_role_prefilter(_vacancy(title="Java Engineer"), company) is False
    assert passes_role_prefilter(_vacancy(title="Kotlin Engineer"), company) is True


def test_default_exclude_list_drops_non_ic_roles() -> None:
    company = _company(role_title_keywords=["software engineer", "backend"])
    assert passes_role_prefilter(_vacancy(title="Product Manager, Platform"), company) is False
    assert passes_role_prefilter(_vacancy(title="Junior Software Engineer"), company) is False
    assert passes_role_prefilter(_vacancy(title="Software Engineer, Backend"), company) is True


def test_java_does_not_match_javascript_title() -> None:
    company = _company(role_title_keywords=["java"])
    assert passes_role_prefilter(_vacancy(title="JavaScript Engineer"), company) is False
    assert passes_role_prefilter(_vacancy(title="Java Engineer"), company) is True


def test_above_seniority_titles_are_rejected_without_explicit_exclude() -> None:
    company = _company(role_title_keywords=["software engineer", "backend"])
    cases = (
        "Staff Software Engineer",
        "Principal Engineer",
        "Staff Backend Engineer",
    )
    for title in cases:
        assert passes_role_prefilter(_vacancy(title=title), company) is False, title


def test_above_seniority_titles_are_rejected_with_empty_include_keywords() -> None:
    company = _company(role_keywords=[], role_title_keywords=[])
    cases = (
        "Staff Software Engineer",
        "Senior Staff Software Engineer",
        "Staff Backend Engineer",
        "Staff Engineer, Backend",
        "Principal Software Engineer",
        "Principal Backend Engineer",
        "Senior Principal Engineer",
    )
    for title in cases:
        assert passes_role_prefilter(_vacancy(title=title), company) is False, title


def test_distinguished_and_fellow_titles_are_outside_this_gate() -> None:
    company = _company(role_title_keywords=[])
    assert passes_role_prefilter(_vacancy(title="Distinguished Engineer"), company) is True
    assert passes_role_prefilter(_vacancy(title="Fellow Engineer"), company) is True


def test_above_seniority_rejection_examines_title_only() -> None:
    company = _company(role_title_keywords=["software engineer", "backend"])
    vacancy = _vacancy(
        title="Senior Software Engineer",
        description="Reports to a Staff Engineer and works with Principal architects.",
    )
    assert passes_role_prefilter(vacancy, company) is True


def test_near_substring_seniority_words_do_not_match() -> None:
    company = _company(role_title_keywords=["software engineer", "backend"])
    assert passes_role_prefilter(_vacancy(title="Staffing Software Engineer"), company) is True
    assert passes_role_prefilter(_vacancy(title="Principality Backend Engineer"), company) is True


def test_target_seniority_titles_still_pass() -> None:
    company = _company(role_title_keywords=["software engineer", "backend", "lead", "engineering manager"])
    cases = (
        "Senior Software Engineer",
        "Senior Backend Engineer",
        "Lead Engineer",
        "Tech Lead",
    )
    for title in cases:
        assert passes_role_prefilter(_vacancy(title=title), company) is True, title


def test_engineering_manager_still_excluded_by_default_list() -> None:
    company = _company(role_title_keywords=["engineering manager"])
    assert passes_role_prefilter(_vacancy(title="Engineering Manager"), company) is False


def _halter_vacancy(title: str) -> NormalizedVacancy:
    return NormalizedVacancy(
        source="target_company:ashby:halter",
        external_id="47506f11-a952-4b34-9672-75195b50566c",
        title=title,
        company="Halter",
        location="Auckland, New Zealand",
        employment=None,
        description="",
        url="https://jobs.ashbyhq.com/halter/47506f11-a952-4b34-9672-75195b50566c",
        published_at=None,
    )


def test_halter_senior_engineers_product_and_engineering_listing_passes() -> None:
    config = load_target_companies_config(DEFAULT_TARGET_COMPANIES_PATH)
    halter = next(company for company in config.companies if company.name == "Halter")

    assert passes_role_prefilter(
        _halter_vacancy("Senior Engineers - Product & Engineering"), halter
    ) is True
    # Punctuation/case-normalized equivalent of the same real listing title.
    assert passes_role_prefilter(
        _halter_vacancy("SENIOR ENGINEERS: PRODUCT & ENGINEERING!"), halter
    ) is True


def test_halter_existing_title_keywords_still_pass() -> None:
    config = load_target_companies_config(DEFAULT_TARGET_COMPANIES_PATH)
    halter = next(company for company in config.companies if company.name == "Halter")

    for title in ("Software Engineer, Backend", "Senior Java Engineer", "Backend Engineer"):
        assert passes_role_prefilter(_halter_vacancy(title), halter) is True, title


def test_halter_still_excludes_unrelated_and_unverified_titles() -> None:
    config = load_target_companies_config(DEFAULT_TARGET_COMPANIES_PATH)
    halter = next(company for company in config.companies if company.name == "Halter")

    cases = (
        "Junior Software Engineer",
        "Graduate Software Engineer",
        "Engineering Manager",
        "iOS Engineer",
        "Hardware Engineer",
        "Data Engineer",
        "NetSuite Administrator",
        "Account Executive",
    )
    for title in cases:
        assert passes_role_prefilter(_halter_vacancy(title), halter) is False, title


def test_halter_extra_phrase_does_not_leak_into_other_companies() -> None:
    config = load_target_companies_config(DEFAULT_TARGET_COMPANIES_PATH)
    partly = next(company for company in config.companies if company.name == "Partly")

    vacancy = NormalizedVacancy(
        source="target_company:ashby:partly.com",
        external_id="1",
        title="Senior Engineers - Product & Engineering",
        company="Partly",
        location="Auckland, New Zealand",
        employment=None,
        description="",
        url="https://jobs.ashbyhq.com/partly.com/1",
        published_at=None,
    )
    assert passes_role_prefilter(vacancy, partly) is False
