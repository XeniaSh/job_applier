from app.collectors.title_filter import evaluate_title, should_accept_title


def test_title_filter_accepts_backend_jvm_titles() -> None:
    assert should_accept_title("Senior Java Backend Engineer")
    assert should_accept_title("Kotlin Spring Developer")
    assert should_accept_title("Java Backend + React")
    assert should_accept_title("Java Full-stack Engineer")


def test_title_filter_rejects_non_target_titles() -> None:
    assert not should_accept_title("Frontend React Developer")
    assert not should_accept_title("QA Tester")
    assert not should_accept_title("Python Backend Engineer")
    assert not should_accept_title("Support Analyst")
    assert not should_accept_title("Mobile Engineer")
    assert not should_accept_title("Data Scientist")
    assert not should_accept_title("ML Engineer")


def test_title_filter_returns_rule_based_reasons() -> None:
    assert evaluate_title("Frontend React Developer").reason == "Frontend title"
    assert evaluate_title("QA Tester").reason == "QA/test role"
    assert evaluate_title("Android Mobile Engineer").reason == "Mobile role"
    assert evaluate_title("Software Engineer, Micro Platforms").reason == "Generic software title without Java/backend evidence"
