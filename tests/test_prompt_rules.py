from pathlib import Path


def test_prompt_requests_russian_human_readable_fields() -> None:
    prompt = Path("prompts/analyze_vacancy.md").read_text(encoding="utf-8")

    assert "All human-readable fields must be in Russian" in prompt
    assert "short_summary" in prompt
    assert "responsibilities" in prompt
    assert "employment_conditions" in prompt
    assert "location_restrictions" in prompt
    assert "uncertainties" in prompt


def test_cover_letter_prompt_requires_coherent_professional_summary() -> None:
    prompt = Path("prompts/create_cover_letter.md").read_text(encoding="utf-8")

    assert "3 to 5 complete sentences" in prompt
    assert "how my experience is relevant to this role" in prompt
    assert "This opportunity aligns..." in prompt
    assert "Never emit" in prompt
    assert "If only the job title is known" in prompt
