from __future__ import annotations

from app.application.autofill.fields import DiscoveredField
from app.application.autofill.answers import ApplicationAnswerGenerator
from app.application.autofill.questions import QuestionKind, is_llm_eligible_question, map_question
from app.application.candidate_profile import CandidateProfile


def _profile() -> CandidateProfile:
    return CandidateProfile.model_validate(
        {
            "identity": {
                "first_name": "Ada",
                "last_name": "Example",
                "email": "ada.example@example.test",
                "phone": "+15555550100",
                "current_location": "Berlin, Germany",
            },
            "employment": {
                "current_title": "Backend Engineer",
                "years_of_experience": 7,
                "professional_tech_stack": ["Java", "Kotlin"],
            },
            "application_files": {"default_resume": "tests/fixtures/autofill/resume.txt"},
        }
    )


class _RecordingLLM:
    def __init__(self, answer: str = "I build backend services in Java.", confident: bool = True) -> None:
        self.calls: list[dict[str, object]] = []
        self.answer = answer
        self.confident = confident

    def create_short_application_answer(self, **kwargs: object) -> tuple[str, bool]:
        self.calls.append(kwargs)
        return self.answer, self.confident


def test_unknown_professional_free_text_is_llm_eligible() -> None:
    field = DiscoveredField(label="Briefly describe your technical background", field_type="textarea", required=True)
    mapped = map_question(field, _profile())
    assert mapped.kind is QuestionKind.PROFESSIONAL_FREE_TEXT
    assert mapped.fillable is False
    assert is_llm_eligible_question(field, mapped) is True


def test_llm_generates_short_answer_for_why_company() -> None:
    llm = _RecordingLLM()
    generator = ApplicationAnswerGenerator(llm)
    field = DiscoveredField(label="Why do you want to work here?", field_type="textarea")
    answer = generator.generate(field, _profile())
    assert answer is not None
    assert "Java" in answer or "backend" in answer.lower()
    assert llm.calls
    assert "Why do you want to work here?" in str(llm.calls[0]["question"])


def test_unsafe_legal_question_is_not_sent_to_llm() -> None:
    llm = _RecordingLLM()
    generator = ApplicationAnswerGenerator(llm)
    field = DiscoveredField(
        label="I consent to the processing of my personal data for recruiting",
        field_type="checkbox",
        required=True,
    )
    assert generator.generate(field, _profile()) is None
    assert llm.calls == []

    salary = DiscoveredField(label="Expected salary", required=True)
    assert generator.generate(salary, _profile()) is None
    relationship = DiscoveredField(
        label="Do you have a personal relationship with a current Agoda employee?",
        field_type="radio",
        options=["Yes", "No"],
    )
    assert generator.generate(relationship, _profile()) is None
    visa = DiscoveredField(label="Will you require visa sponsorship?")
    assert generator.generate(visa, _profile()) is None
    deloitte = DiscoveredField(
        label="Please confirm if you are currently or formerly associated with Deloitte or any of its subsidiary entities",
        field_type="select",
        options=["Yes, I am", "No, I am not a current/an ex-employee of Deloitte"],
    )
    assert generator.generate(deloitte, _profile()) is None
    source = DiscoveredField(
        label="How did you hear about this job?",
        field_type="select",
        options=["Company Website", "LinkedIn"],
    )
    assert generator.generate(source, _profile()) is None
    blog = DiscoveredField(
        label="Have you read our engineering blog?",
        field_type="select",
        options=["Yes", "No"],
    )
    assert generator.generate(blog, _profile()) is None
    gender = DiscoveredField(
        label="Gender*",
        field_type="select",
        options=["Prefer not to disclose", "Female", "Male"],
        required=True,
    )
    assert generator.generate(gender, _profile()) is None
    sms = DiscoveredField(
        label="Do you allow us to provide you TEXT/SMS updates of your interview process?",
        field_type="select",
        options=["Yes", "No"],
    )
    assert generator.generate(sms, _profile()) is None
    assert llm.calls == []


def test_llm_select_answer_must_match_available_options() -> None:
    llm = _RecordingLLM(answer="Quantum computing")
    generator = ApplicationAnswerGenerator(llm)
    field = DiscoveredField(
        label="What interests you about this role?",
        field_type="select",
        options=["Backend services", "Mobile apps"],
    )
    assert generator.generate(field, _profile()) is None
    llm.answer = "Backend services"
    assert generator.generate(field, _profile()) == "Backend services"


def test_unconfident_llm_answer_is_discarded() -> None:
    llm = _RecordingLLM(answer="Maybe this.", confident=False)
    generator = ApplicationAnswerGenerator(llm)
    field = DiscoveredField(label="Why do you want to work here?", field_type="textarea")
    assert generator.generate(field, _profile()) is None
