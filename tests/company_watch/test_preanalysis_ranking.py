from app.company_watch.preanalysis_ranking import rank_title_for_analysis


def test_senior_java_backend_ranks_above_principal_compiler() -> None:
    java_backend = rank_title_for_analysis("Senior Java Backend Engineer")
    compiler = rank_title_for_analysis("Principal Java Engineer - Compiler")
    assert java_backend.score > compiler.score


def test_java_and_backend_titles_get_boost() -> None:
    both = rank_title_for_analysis("Java Backend Engineer")
    java_only = rank_title_for_analysis("Java Engineer")
    generic = rank_title_for_analysis("Platform Engineer")
    assert both.score > java_only.score > generic.score


def test_compiler_kernel_frontend_fullstack_data_get_penalty() -> None:
    baseline = rank_title_for_analysis("Senior Java Backend Engineer").score
    assert rank_title_for_analysis("Senior Compiler Developer (Kotlin Compiler - Core)").score < baseline
    assert rank_title_for_analysis("Linux Kernel Engineer").score < baseline
    assert rank_title_for_analysis("Frontend Engineer").score < baseline
    assert rank_title_for_analysis("Full-Stack Engineer").score < baseline
    assert rank_title_for_analysis("Senior Data Engineer").score < baseline
    assert rank_title_for_analysis("Data Platform Engineer").score < baseline
    assert rank_title_for_analysis("Machine Learning Engineer").score < baseline
    assert rank_title_for_analysis("QA Automation Engineer").score < baseline


def test_staff_plus_is_penalized_but_not_excluded() -> None:
    senior = rank_title_for_analysis("Senior Java Engineer")
    principal = rank_title_for_analysis("Principal Java Engineer")
    compiler = rank_title_for_analysis("Senior Compiler Developer")
    assert principal.score < senior.score
    assert principal.score > compiler.score
    assert principal.seniority == "STAFF_PLUS"


def test_equal_titles_keep_same_score_for_stable_ties() -> None:
    first = rank_title_for_analysis("Java Engineer")
    second = rank_title_for_analysis("Java Engineer")
    assert first.score == second.score
