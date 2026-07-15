from app.collectors.title_filter import should_accept_title


def test_title_filter_accepts_backend_jvm_titles() -> None:
    assert should_accept_title("Senior Java Backend Engineer")
    assert should_accept_title("Kotlin Spring Developer")
    assert should_accept_title("Java Backend + React")


def test_title_filter_rejects_non_target_titles() -> None:
    assert not should_accept_title("Frontend React Developer")
    assert not should_accept_title("QA Tester")
    assert not should_accept_title("Python Backend Engineer")
    assert not should_accept_title("Support Analyst")
