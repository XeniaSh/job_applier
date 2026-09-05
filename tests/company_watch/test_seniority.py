from app.company_watch.seniority import classify_seniority


def test_senior_backend_engineer_is_senior() -> None:
    result = classify_seniority("Senior Backend Engineer")
    assert result.label == "SENIOR"


def test_staff_java_engineer_is_staff_plus() -> None:
    result = classify_seniority("Staff Java Engineer")
    assert result.label == "STAFF_PLUS"


def test_principal_software_engineer_is_staff_plus() -> None:
    result = classify_seniority("Principal Software Engineer")
    assert result.label == "STAFF_PLUS"


def test_software_engineer_i_is_junior() -> None:
    result = classify_seniority("Software Engineer I (Java)")
    assert result.label == "JUNIOR"


def test_engineering_manager_is_lead_manager() -> None:
    result = classify_seniority("Engineering Manager")
    assert result.label == "LEAD_MANAGER"


def test_graduate_software_engineer_is_junior() -> None:
    result = classify_seniority("Graduate Software Engineer")
    assert result.label == "JUNIOR"


def test_intern_is_intern() -> None:
    result = classify_seniority("Software Engineering Intern")
    assert result.label == "INTERN"


def test_team_lead_is_lead_manager() -> None:
    result = classify_seniority("Java Team Lead")
    assert result.label == "LEAD_MANAGER"


def test_senior_engineering_manager_is_lead_manager_not_senior() -> None:
    result = classify_seniority("Senior Engineering Manager")
    assert result.label == "LEAD_MANAGER"


def test_title_without_level_is_unknown() -> None:
    result = classify_seniority("Java Backend Engineer")
    assert result.label == "UNKNOWN"


def test_engineer_ii_is_mid() -> None:
    result = classify_seniority("Software Engineer II")
    assert result.label == "MID"
