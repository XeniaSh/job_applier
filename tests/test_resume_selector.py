import json
from pathlib import Path

from app.resume_profiles import load_resume_profiles
from app.resume_selector import ResumeSelector


class _FakeLLM:
    def __init__(self, response: dict[str, str]) -> None:
        self.response = response
        self.prompt: str | None = None

    def select_resume_profile(self, *, prompt: str) -> str:
        self.prompt = prompt
        return json.dumps(self.response)


def _write_profiles(path: Path) -> None:
    path.write_text(
        """
profiles:
  - id: java
    description: General Java backend profile.
    pdf: resumes/java.pdf
  - id: java_ai
    description: Java backend profile with AI workflow automation.
    pdf: resumes/java_ai.pdf
  - id: fintech
    description: Backend profile emphasizing banking and payments.
    pdf: resumes/fintech.pdf
""".strip(),
        encoding="utf-8",
    )


def test_loads_any_number_of_profiles_and_formats_plain_text(tmp_path: Path) -> None:
    config_path = tmp_path / "resume_profiles.yaml"
    _write_profiles(config_path)

    profiles = load_resume_profiles(config_path)

    assert profiles.ids == ("java", "java_ai", "fintech")
    assert profiles.get("java_ai").pdf == tmp_path / "resumes/java_ai.pdf"  # type: ignore[union-attr]
    formatted = profiles.format_for_prompt()
    assert "Profile ID: java_ai" in formatted
    assert "Description:\nJava backend profile with AI workflow automation." in formatted
    assert "profiles:" not in formatted


def test_selector_inserts_profiles_and_job_description(tmp_path: Path) -> None:
    config_path = tmp_path / "resume_profiles.yaml"
    _write_profiles(config_path)
    llm = _FakeLLM(
        {
            "selected_profile": "java_ai",
            "reason": "The role centers on AI-powered developer workflows.",
        }
    )
    selector = ResumeSelector(
        llm_client=llm,
        profiles=load_resume_profiles(config_path),
        prompt_template="PROFILES\n{{ resume_profiles }}\nJOB\n{{ job_description }}",
    )

    result = selector.select("Build AI-assisted engineering tools.")

    assert result.selected_profile == "java_ai"
    assert llm.prompt is not None
    assert "Profile ID: java_ai" in llm.prompt
    assert "Build AI-assisted engineering tools." in llm.prompt
    assert "{{ resume_profiles }}" not in llm.prompt


def test_unknown_model_selection_falls_back_to_java(tmp_path: Path, caplog) -> None:
    config_path = tmp_path / "resume_profiles.yaml"
    _write_profiles(config_path)
    selector = ResumeSelector(
        llm_client=_FakeLLM(
            {
                "selected_profile": "unknown",
                "reason": "Unsupported choice.",
            }
        ),
        profiles=load_resume_profiles(config_path),
        prompt_template="{{ resume_profiles }}\n{{ job_description }}",
    )

    with caplog.at_level("INFO"):
        result = selector.select("General backend role")

    assert result.selected_profile == "java"
    assert "unknown profile" in result.reason
    assert "Selected resume profile: java" in caplog.text
    assert "reason:" in caplog.text
