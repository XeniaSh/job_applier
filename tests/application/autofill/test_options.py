from __future__ import annotations

from app.application.autofill.options import (
    find_known_technology_in_text,
    label_matches,
    match_academic_option,
    match_affirmative_option,
    match_application_source,
    match_gender_option,
    match_option,
    match_prefer_not_to_disclose_gender,
    match_years_option,
    match_yes_no,
    parse_located_in_places,
    parse_max_choices,
    parse_relocation_destination,
    parse_sponsorship_scope,
    parse_years_experience_technology,
    match_sponsorship_option,
    match_work_authorization_option,
    select_listed_options,
)


def test_parse_max_choices_and_relocation_destination() -> None:
    assert parse_max_choices("select top 3 technologies") == 3
    assert parse_relocation_destination(
        "Are you currently based in Bangkok or open to relocate to Bangkok?"
    ) == "Bangkok"


def test_parse_located_in_places_splits_named_countries() -> None:
    assert parse_located_in_places("Are you located in the UK or Poland?") == ["UK", "Poland"]
    assert parse_located_in_places("Do you reside in Germany?") == ["Germany"]
    assert parse_located_in_places("Are you willing to relocate to the UK or Poland?") == []


def test_years_and_academic_matching() -> None:
    options = ["0-2 years", "3-5 years", "6-8 years", "9+ years"]
    assert match_years_option(7, options) == "6-8 years"
    assert match_years_option(10, options) == "9+ years"
    assert match_academic_option("Master's", ["High school", "Bachelor's", "Master's", "PhD"]) == "Master's"


def test_parse_years_experience_technology_alternate_phrasings() -> None:
    assert parse_years_experience_technology("Minimum Years of experience in Kotlin *") == "Kotlin"
    assert parse_years_experience_technology("How many years of Kotlin experience?") == "Kotlin"
    assert parse_years_experience_technology("Rust experience (years)") == "Rust"
    assert (
        parse_years_experience_technology("Years of experience using Spring Boot development")
        == "Spring Boot"
    )
    assert parse_years_experience_technology("What is your overall years of relevant experience?") is None
    assert parse_years_experience_technology("Total years of experience in the industry") is None


def test_parse_years_experience_technology_keeps_full_comma_separated_phrase() -> None:
    """The captured phrase must not be truncated at a comma: dropping "Kotlin"
    from "Java, Kotlin" would let a single-technology answer (e.g. only Java
    configured) silently fill a combined question.
    """
    assert (
        parse_years_experience_technology("Years of experience with Java, Kotlin")
        == "Java, Kotlin"
    )


def test_find_known_technology_in_text_prefers_longer_configured_name() -> None:
    known = ["Spring", "Spring Boot", "Kafka"]
    assert find_known_technology_in_text("Kotlin experience (years)", known) is None
    assert find_known_technology_in_text("Spring Boot experience (years)", known) == "Spring Boot"
    assert find_known_technology_in_text("Kafka years", known) == "Kafka"


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
    assert match_prefer_not_to_disclose_gender(["Female", "Male", "Decline To Self Identify"]) == (
        "Decline To Self Identify"
    )
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


def test_match_work_authorization_accepts_descriptive_negative_label() -> None:
    options = [
        "I am legally authorized to work in the United States",
        "I am not legally authorized to work in the United States",
    ]
    assert match_work_authorization_option(False, options) == options[1]
    assert match_work_authorization_option(True, options) == options[0]


def test_match_affirmative_option_accepts_acknowledge_confirm() -> None:
    options = ["Acknowledge/Confirm"]
    assert match_yes_no(True, options) is None
    assert match_affirmative_option(True, options) == "Acknowledge/Confirm"
    assert match_affirmative_option(True, ["Please select", "I acknowledge"]) == "I acknowledge"
    assert match_affirmative_option(True, ["Yes", "No"]) == "Yes"


def test_match_affirmative_option_accepts_i_understand_with_privacy_cue() -> None:
    options = [
        "Please select",
        "I understand that my personal data will be processed in accordance "
        "with Wolt’s recruitment privacy statement.",
    ]
    assert match_affirmative_option(True, options) == options[1]


def test_match_affirmative_option_rejects_unrelated_i_understand() -> None:
    options = [
        "Please select",
        "I understand this role requires occasional weekend on-call shifts.",
    ]
    assert match_affirmative_option(True, options) is None


def test_match_application_source_prefers_website_over_linkedin() -> None:
    options = ["Employee Referral", "LinkedIn", "Company Careers Site", "Other"]
    assert match_application_source(options, ["Company Website", "LinkedIn", "Other"]) == "Company Careers Site"


def test_match_application_source_skips_forbidden_even_if_listed() -> None:
    options = ["Employee Referral", "Recruiter", "LinkedIn"]
    assert match_application_source(options, ["Employee Referral", "LinkedIn"]) == "LinkedIn"


def test_parse_sponsorship_scope_distinguishes_current_location() -> None:
    assert parse_sponsorship_scope(
        "Will you now or in the future require sponsorship for a visa to remain in your current location?"
    ) == ("current", None)
    assert parse_sponsorship_scope("Will you now or in the future require visa sponsorship?") == (
        "generic",
        None,
    )
    assert parse_sponsorship_scope("Will you require visa sponsorship to work in the Netherlands?") == (
        "country",
        "netherlands",
    )


def test_match_sponsorship_option_does_not_pick_unrelated_hsm() -> None:
    options = ["Yes, Netherlands Highly Skilled Migrant Visa", "No"]
    assert match_sponsorship_option(True, options, referenced_country="uzbekistan") is None
    assert match_sponsorship_option(True, options, referenced_country=None) is None
    assert match_sponsorship_option(True, options, referenced_country="netherlands") == (
        "Yes, Netherlands Highly Skilled Migrant Visa"
    )
    assert match_sponsorship_option(False, options, referenced_country="uzbekistan") == "No"
    assert match_sponsorship_option(True, ["Yes", "No"], referenced_country=None) == "Yes"
