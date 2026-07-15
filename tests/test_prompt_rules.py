from pathlib import Path


def test_prompt_requests_russian_human_readable_fields() -> None:
    prompt = Path("prompts/analyze_vacancy.md").read_text(encoding="utf-8")

    assert "All human-readable fields must be in Russian" in prompt
    assert "short_summary" in prompt
    assert "responsibilities" in prompt
    assert "employment_conditions" in prompt
    assert "location_restrictions" in prompt
    assert "uncertainties" in prompt
