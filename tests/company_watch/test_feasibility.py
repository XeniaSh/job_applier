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
