from app.company_watch.feasibility import assess_application_feasibility


def test_likely_when_visa_sponsorship_mentioned() -> None:
    result = assess_application_feasibility(
        vacancy_text="Java backend role. Visa sponsorship is available for this position.",
        location="Amsterdam",
    )

    assert result.label == "LIKELY"
    assert result.visa_sponsorship == "yes"
    assert "visa sponsorship mentioned" in result.warnings


def test_likely_when_relocation_support_mentioned() -> None:
    result = assess_application_feasibility(
        vacancy_text="We offer a relocation package and help with moving.",
        location="Madrid",
    )

    assert result.label == "LIKELY"
    assert result.relocation_support == "yes"
    assert "relocation support mentioned" in result.warnings


def test_likely_when_remote_worldwide() -> None:
    result = assess_application_feasibility(
        vacancy_text="This is a worldwide remote role. Work from anywhere.",
        location="Remote",
    )

    assert result.label == "LIKELY"
    assert result.remote_type == "worldwide"
    assert "remote worldwide" in result.warnings


def test_likely_from_extracted_visa_field() -> None:
    result = assess_application_feasibility(
        vacancy_text="Java engineer in Sao Jose dos Campos.",
        location="Sao Jose dos Campos",
        visa_sponsorship="yes",
    )

    assert result.label == "LIKELY"
    assert result.visa_sponsorship == "yes"


def test_unlikely_when_local_work_authorization_required() -> None:
    result = assess_application_feasibility(
        vacancy_text="Candidates must be authorized to work in the United States. We cannot sponsor visas.",
        location="Chicago",
    )

    assert result.label == "UNLIKELY"
    assert result.work_authorization_requirement == "required"
    assert result.visa_sponsorship == "no"
    assert "local work authorization required" in result.warnings


def test_unclear_when_no_feasibility_data() -> None:
    result = assess_application_feasibility(
        vacancy_text="Java backend services and distributed systems.",
        location="Sao Jose dos Campos",
    )

    assert result.label == "UNCLEAR"
    assert result.visa_sponsorship == "unknown"
    assert result.relocation_support == "unknown"
    assert "visa/relocation not mentioned; check manually" in result.warnings


def test_worldwide_remote_is_likely_even_without_visa() -> None:
    result = assess_application_feasibility(
        vacancy_text="Fully remote worldwide engineering role.",
        location=None,
        visa_sponsorship="unknown",
    )

    assert result.label == "LIKELY"
    assert result.remote_type == "worldwide"


def test_unclear_when_only_physical_location_is_required_nz() -> None:
    result = assess_application_feasibility(
        vacancy_text="This role must be based in New Zealand for compliance reasons.",
        location="Wellington",
    )

    assert result.work_authorization_requirement == "unknown"
    assert result.visa_sponsorship == "unknown"
    assert result.label == "UNCLEAR"
    assert "visa/relocation not mentioned; check manually" in result.warnings


def test_unclear_when_only_physical_location_is_required_auckland() -> None:
    result = assess_application_feasibility(
        vacancy_text="Candidates must reside in Auckland to be considered.",
        location="Auckland",
    )

    assert result.work_authorization_requirement == "unknown"
    assert result.label == "UNCLEAR"


def test_unclear_when_only_physical_location_is_required_germany() -> None:
    result = assess_application_feasibility(
        vacancy_text="Applicants must be located in Germany for this position.",
        location="Berlin",
    )

    assert result.work_authorization_requirement == "unknown"
    assert result.label == "UNCLEAR"


def test_unclear_when_only_physical_location_is_required_singapore() -> None:
    result = assess_application_feasibility(
        vacancy_text="You must currently live in Singapore to apply.",
        location="Singapore",
    )

    assert result.work_authorization_requirement == "unknown"
    assert result.label == "UNCLEAR"


def test_required_when_explicit_eligibility_text_present_regardless_of_location() -> None:
    result = assess_application_feasibility(
        vacancy_text="Must be based in New Zealand. Applicants must be eligible to work in New Zealand.",
        location="Auckland",
    )

    assert result.work_authorization_requirement == "required"
    assert result.label == "UNLIKELY"


def test_required_when_legal_right_to_work_phrase_present() -> None:
    result = assess_application_feasibility(
        vacancy_text="Candidates must have a legal right to work in Germany.",
        location="Berlin",
    )

    assert result.work_authorization_requirement == "required"
    assert result.label == "UNLIKELY"


def test_unclear_when_only_bare_residents_only_phrase_present() -> None:
    result = assess_application_feasibility(
        vacancy_text="Singapore residents only may apply for this role.",
        location="Singapore",
    )

    assert result.work_authorization_requirement == "unknown"
    assert result.label == "UNCLEAR"


def test_required_when_permanent_residents_only_phrase_present() -> None:
    result = assess_application_feasibility(
        vacancy_text="Singapore permanent residents only may apply for this role.",
        location="Singapore",
    )

    assert result.work_authorization_requirement == "required"
    assert result.label == "UNLIKELY"
