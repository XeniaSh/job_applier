from __future__ import annotations

from app.application.autofill.options import (
    label_matches,
    match_academic_option,
    match_affirmative_option,
    match_application_source,
    match_gender_option,
    match_option,
    match_prefer_not_to_disclose_gender,
    match_years_option,
    match_yes_no,
    parse_max_choices,
    parse_relocation_destination,
    select_listed_options,
)


def test_parse_max_choices_and_relocation_destination() -> None:
    assert parse_max_choices("select top 3 technologies") == 3
    assert parse_relocation_destination(
        "Are you currently based in Bangkok or open to relocate to Bangkok?"
    ) == "Bangkok"


def test_years_and_academic_matching() -> None:
    options = ["0-2 years", "3-5 years", "6-8 years", "9+ years"]
    assert match_years_option(7, options) == "6-8 years"
    assert match_years_option(10, options) == "9+ years"
    assert match_academic_option("Master's", ["High school", "Bachelor's", "Master's", "PhD"]) == "Master's"


def test_academic_option_maps_semantic_levels_without_diploma_fallback() -> None:
    options = [
        "Highschool",
        "Diploma",
        "Bachelor's Degree",
        "Master's Degree",
        "Doctorate Degree",
    ]
    assert match_academic_option("MASTERS", options) == "Master's Degree"
    assert match_academic_option("specialist", options) == "Master's Degree"
    assert match_academic_option("Master's", options) == "Master's Degree"
    assert match_academic_option("bachelor", options) == "Bachelor's Degree"
    assert match_academic_option("DOCTORATE", options) == "Doctorate Degree"
    assert match_academic_option("Diploma", options) == "Diploma"
    assert match_academic_option("MASTERS", options) != "Diploma"


def test_select_listed_options_respects_max_and_form_options() -> None:
    selected = select_listed_options(
        ["Java", "Kotlin", "Spring Boot", "PostgreSQL"],
        ["Java", "Python", "Go", "Kotlin", "Ruby"],
        max_choices=3,
    )
    assert selected == ["Java", "Kotlin"]


def test_java_does_not_match_javascript() -> None:
    options = ["Javascript", "Java", "Kotlin", "Python"]
    assert match_option("Java", options) == "Java"
    assert "Javascript" not in select_listed_options(
        ["Java", "Kotlin"],
        options,
        max_choices=3,
    )
    assert select_listed_options(["Java", "Kotlin"], options, max_choices=3) == ["Java", "Kotlin"]
    assert not label_matches("Java", "Javascript")
    assert not label_matches("Javascript", "Java")
    assert label_matches("No", "No, I am not a current employee")


def test_top_three_does_not_force_a_third_unmatched_skill() -> None:
    selected = select_listed_options(
        ["Java", "Kotlin", "Spring Boot"],
        ["Javascript", "Java", "Kotlin", "Python"],
        max_choices=3,
    )
    assert selected == ["Java", "Kotlin"]


def test_select_listed_options_picks_top_three_truthful_matches() -> None:
    selected = select_listed_options(
        ["Java", "Kotlin", "Spring Boot", "PostgreSQL", "Kafka"],
        ["Javascript", "Java", "Python", "Go", "Kotlin", "Spring Boot", "Ruby"],
        max_choices=3,
    )
    assert selected == ["Java", "Kotlin", "Spring Boot"]
    assert "Javascript" not in selected


def test_match_gender_prefers_not_to_disclose_policy() -> None:
    options = ["Female", "Genderqueer", "Male", "Prefer not to disclose"]
    assert match_prefer_not_to_disclose_gender(options) == "Prefer not to disclose"
    assert match_gender_option("prefer not to disclose", options) == "Prefer not to disclose"
    assert match_gender_option("prefer not to say", ["Prefer not to say", "Female"]) == "Prefer not to say"
    assert match_prefer_not_to_disclose_gender(["Female", "Male"]) is None


def test_match_gender_option_uses_explicit_value_not_decline() -> None:
    options = ["Prefer not to disclose", "Female", "Male", "Non-binary"]
    assert match_gender_option("female", options) == "Female"
    assert match_gender_option("Woman", options) == "Female"
    assert "prefer not" not in match_gender_option("Female", options).lower()


def test_match_yes_no_uses_visible_labels_not_true_false() -> None:
    options = ["Yes", "No"]
    assert match_yes_no(True, options) == "Yes"
    assert match_yes_no(False, options) == "No"
    long_no = [
        "Yes, I am a current/an ex-employee of Deloitte or its subsidiaries",
        "No, I am not a current/an ex-employee of Deloitte or any of its subsidiary entities",
    ]
    assert match_yes_no(False, long_no).startswith("No")
    assert match_yes_no(True, long_no).startswith("Yes")


def test_match_affirmative_option_accepts_acknowledge_confirm() -> None:
    options = ["Acknowledge/Confirm"]
    assert match_yes_no(True, options) is None
    assert match_affirmative_option(True, options) == "Acknowledge/Confirm"
    assert match_affirmative_option(True, ["Please select", "I acknowledge"]) == "I acknowledge"
    assert match_affirmative_option(True, ["Yes", "No"]) == "Yes"


def test_match_application_source_prefers_website_over_linkedin() -> None:
    options = ["Employee Referral", "LinkedIn", "Company Careers Site", "Other"]
    assert match_application_source(options, ["Company Website", "LinkedIn", "Other"]) == "Company Careers Site"


def test_match_application_source_skips_forbidden_even_if_listed() -> None:
    options = ["Employee Referral", "Recruiter", "LinkedIn"]
    assert match_application_source(options, ["Employee Referral", "LinkedIn"]) == "LinkedIn"
