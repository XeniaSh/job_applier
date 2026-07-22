import json
import logging

import httpx
import pytest
import respx

from app.llm_client import (
    CoverLetterValidationError,
    LLMClient,
    LLMRequestError,
    LLMResponseError,
    _apply_soft_cover_letter_cleanup,
    _count_complete_sentences,
    _validate_cover_letter,
)
from app.models import (
    CoverLetterResult,
    Decision,
    RecommendedCoverTemplate,
    RecommendedResume,
    VacancyEvaluation,
)

_VALID_EN_SUMMARY = (
    "I am a Java Backend Engineer with around seven years of experience developing applications "
    "using Java, Spring Boot, and Kafka. I have built and maintained production services and "
    "integrations for distributed systems. My experience is relevant to this Java Backend Engineer "
    "role because it focuses on the same core technologies."
)


def _chat_response(content: str, status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code=status_code,
        json={"choices": [{"message": {"content": content}}]},
    )


def _valid_json_payload() -> str:
    return json.dumps(
        {
            "mandatory_skills": ["Java", "Spring Boot"],
            "optional_skills": ["Redis"],
            "minimum_experience_years": 6,
            "seniority": "Senior",
            "responsibilities": ["Design backend services"],
            "employment_conditions": ["Full-time"],
            "location_restrictions": ["EU timezone overlap"],
            "uncertainties": ["B2B contract details"],
            "role_type": "Java Backend Engineer",
            "short_summary": "Product backend role focused on Java services.",
        }
    )


@respx.mock
def test_successful_valid_llm_json() -> None:
    route = respx.post("https://llm.local/chat/completions").mock(
        return_value=_chat_response(_valid_json_payload())
    )
    client = LLMClient(api_url="https://llm.local", api_key="secret", model="test-model")

    result = client.extract_vacancy(prompt="PROMPT", vacancy="VACANCY")

    assert route.called
    assert result.role_type == "Java Backend Engineer"
    assert result.mandatory_skills == ["java", "spring boot"]
    assert result.minimum_experience_years == 6


@respx.mock
def test_malformed_json_then_successful_retry() -> None:
    respx.post("https://llm.local/chat/completions").mock(
        side_effect=[
            _chat_response("{bad json"),
            _chat_response(_valid_json_payload()),
        ]
    )
    client = LLMClient(api_url="https://llm.local", api_key="secret", model="test-model")

    result = client.extract_vacancy(prompt="PROMPT", vacancy="VACANCY")

    assert result.role_type == "Java Backend Engineer"


@respx.mock
def test_two_malformed_responses() -> None:
    route = respx.post("https://llm.local/chat/completions").mock(
        side_effect=[
            _chat_response("{bad json"),
            _chat_response("{still bad json"),
        ]
    )
    client = LLMClient(api_url="https://llm.local", api_key="secret", model="test-model")

    with pytest.raises(LLMResponseError):
        client.extract_vacancy(prompt="PROMPT", vacancy="VACANCY")
    assert route.call_count == 2


@respx.mock
def test_api_timeout() -> None:
    respx.post("https://llm.local/chat/completions").mock(
        side_effect=httpx.ReadTimeout("timed out")
    )
    client = LLMClient(api_url="https://llm.local", api_key="secret", model="test-model")

    with pytest.raises(LLMRequestError):
        client.extract_vacancy(prompt="PROMPT", vacancy="VACANCY")


@respx.mock
def test_payload_contains_temperature_and_max_tokens() -> None:
    route = respx.post("https://llm.local/chat/completions").mock(
        return_value=_chat_response(_valid_json_payload())
    )
    client = LLMClient(api_url="https://llm.local", api_key="secret", model="test-model")

    client.extract_vacancy(prompt="PROMPT", vacancy="VACANCY")

    request_payload = json.loads(route.calls.last.request.content)
    assert request_payload["temperature"] == 0
    assert request_payload["max_tokens"] == 1200
    assert request_payload["response_format"] == {"type": "json_object"}


@respx.mock
def test_request_timing_logged_without_sensitive_data(caplog: pytest.LogCaptureFixture) -> None:
    route = respx.post("https://llm.local/chat/completions").mock(
        return_value=_chat_response(_valid_json_payload())
    )
    client = LLMClient(api_url="https://llm.local", api_key="super-secret", model="test-model")
    vacancy_text = "Sensitive vacancy details"

    with caplog.at_level(logging.INFO):
        client.extract_vacancy(prompt="PROMPT", vacancy=vacancy_text)

    assert route.called
    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert "LLM request took " in log_text
    assert "super-secret" not in log_text
    assert vacancy_text not in log_text


def _evaluation() -> VacancyEvaluation:
    return VacancyEvaluation(
        decision=Decision.POTENTIAL_MATCH,
        summary="summary",
        matched_points=["java"],
        gaps=[],
        nuances=[],
        match_percentage=None,
        matched_score=0.0,
        total_possible_score=0.0,
        explicit_skill_count=2,
        evidence_sufficient=False,
        recommended_resume=RecommendedResume.JAVA_BACKEND,
        recommended_cover_template=RecommendedCoverTemplate.GENERIC,
    )


@respx.mock
def test_cover_letter_malformed_json_retry_and_payload() -> None:
    route = respx.post("https://llm.local/chat/completions").mock(
        side_effect=[
            _chat_response("{bad"),
            _chat_response(
                json.dumps(
                    {
                        "language": "en",
                        "cover_letter": _VALID_EN_SUMMARY,
                        "used_resume": "java-backend",
                    }
                )
            ),
        ]
    )
    client = LLMClient(api_url="https://llm.local", api_key="secret", model="test-model")
    result = client.create_cover_letter(
        prompt="PROMPT",
        candidate_profile="Java Backend Engineer with around seven years of commercial backend experience.",
        vacancy_text="vacancy",
        analysis=_evaluation(),
        recommended_resume="java-backend",
    )
    assert route.call_count == 2
    assert result.language == "en"
    assert "redis" not in result.cover_letter.lower()
    assert "i am a senior" not in result.cover_letter.lower()
    request_payload = json.loads(route.calls.last.request.content)
    assert request_payload["temperature"] == 0.2
    assert request_payload["max_tokens"] == 500


@respx.mock
def test_comfortable_with_concurrency_rejected() -> None:
    respx.post("https://llm.local/chat/completions").mock(
        side_effect=[
            _chat_response(
                json.dumps(
                    {
                        "language": "en",
                        "cover_letter": (
                            "I have around seven years of Java backend experience. "
                            "I am comfortable with concurrency and Spring Boot."
                        ),
                        "used_resume": "java-backend",
                    }
                )
            ),
            _chat_response(
                json.dumps(
                    {
                        "language": "en",
                        "cover_letter": (
                            "I am a Java Backend Engineer with around seven years of experience with Java and Spring Boot. "
                            "I have practical experience with multithreading and concurrent programming in Java. "
                            "My background is relevant to this Java Backend Engineer role."
                        ),
                        "used_resume": "java-backend",
                    }
                )
            ),
        ]
    )
    client = LLMClient(api_url="https://llm.local", api_key="secret", model="test-model")
    result = client.create_cover_letter(
        prompt="PROMPT",
        candidate_profile="Java Backend Engineer with 7 years of commercial backend experience.",
        vacancy_text="Title: Java Backend Engineer\nContent completeness: PARTIAL",
        analysis=_evaluation(),
        recommended_resume="java-backend",
    )
    assert "comfortable with" not in result.cover_letter.lower()


@respx.mock
def test_six_years_and_six_wording_rejected() -> None:
    route = respx.post("https://llm.local/chat/completions").mock(
        side_effect=[
            _chat_response(
                json.dumps(
                    {
                        "language": "en",
                        "cover_letter": "I have 6 years of Java backend experience with Spring Boot.",
                        "used_resume": "java-backend",
                    }
                )
            ),
            _chat_response(
                json.dumps(
                    {
                        "language": "en",
                        "cover_letter": _VALID_EN_SUMMARY,
                        "used_resume": "java-backend",
                    }
                )
            ),
        ]
    )
    client = LLMClient(api_url="https://llm.local", api_key="secret", model="test-model")
    result = client.create_cover_letter(
        prompt="PROMPT",
        candidate_profile="Java Backend Engineer with seven years of commercial backend experience.",
        vacancy_text="Title: Java Backend Engineer",
        analysis=_evaluation(),
        recommended_resume="java-backend",
    )
    assert "around seven years" in result.cover_letter.lower()
    assert route.call_count == 2


@respx.mock
def test_candidate_not_called_senior_or_lead() -> None:
    route = respx.post("https://llm.local/chat/completions").mock(
        side_effect=[
            _chat_response(
                json.dumps(
                    {
                        "language": "en",
                        "cover_letter": "I am a Senior Java Developer with around seven years of experience.",
                        "used_resume": "java-backend",
                    }
                )
            ),
            _chat_response(
                json.dumps(
                    {
                        "language": "en",
                        "cover_letter": (
                            "I am a Java Backend Engineer with around seven years of experience with Java, "
                            "Spring Boot and microservices. I have maintained production services and integrations. "
                            "My experience is relevant to this Java/Kotlin backend role."
                        ),
                        "used_resume": "java-backend",
                    }
                )
            ),
        ]
    )
    client = LLMClient(api_url="https://llm.local", api_key="secret", model="test-model")
    result = client.create_cover_letter(
        prompt="PROMPT",
        candidate_profile="Java Backend Engineer with 7 years of commercial backend experience.",
        vacancy_text="Title: Backend Lead (Java/Kotlin)\nContent completeness: PARTIAL",
        analysis=_evaluation(),
        recommended_resume="java-backend",
    )
    lower = result.cover_letter.lower()
    assert "i am a senior" not in lower
    assert "lead developer" not in lower
    assert route.call_count == 2


@respx.mock
def test_no_more_than_four_technologies_and_no_redis() -> None:
    route = respx.post("https://llm.local/chat/completions").mock(
        side_effect=[
            _chat_response(
                json.dumps(
                    {
                        "language": "en",
                        "cover_letter": (
                            "I have around seven years with Java, Spring Boot, Kafka, PostgreSQL, Docker, "
                            "Kubernetes and Redis in backend systems."
                        ),
                        "used_resume": "java-backend",
                    }
                )
            ),
            _chat_response(
                json.dumps(
                    {
                        "language": "en",
                        "cover_letter": _VALID_EN_SUMMARY,
                        "used_resume": "java-backend",
                    }
                )
            ),
        ]
    )
    client = LLMClient(api_url="https://llm.local", api_key="secret", model="test-model")
    result = client.create_cover_letter(
        prompt="PROMPT",
        candidate_profile="Java Backend Engineer with 7 years of commercial backend experience.",
        vacancy_text="Content completeness: PARTIAL",
        analysis=_evaluation(),
        recommended_resume="java-backend",
    )
    lower = result.cover_letter.lower()
    assert "redis" not in lower
    assert route.call_count == 2


@respx.mock
def test_english_and_russian_word_limits() -> None:
    respx.post("https://llm.local/chat/completions").mock(
        side_effect=[
            _chat_response(
                json.dumps(
                    {
                        "language": "en",
                        "cover_letter": " ".join(["word"] * 150) + " around seven years",
                        "used_resume": "java-backend",
                    }
                )
            ),
            _chat_response(
                json.dumps(
                    {
                        "language": "en",
                        "cover_letter": _VALID_EN_SUMMARY,
                        "used_resume": "java-backend",
                    }
                )
            ),
        ]
    )
    client = LLMClient(api_url="https://llm.local", api_key="secret", model="test-model")
    result = client.create_cover_letter(
        prompt="PROMPT",
        candidate_profile="Java Backend Engineer with 7 years of commercial backend experience.",
        vacancy_text="Content completeness: PARTIAL",
        analysis=_evaluation(),
        recommended_resume="java-backend",
    )
    assert result.language == "en"
    assert len(result.cover_letter.split()) <= 80


@respx.mock
def test_second_hard_invalid_response_raises_validation_error() -> None:
    route = respx.post("https://llm.local/chat/completions").mock(
        side_effect=[
            _chat_response(
                json.dumps(
                    {
                        "language": "en",
                        "cover_letter": "I have 6 years of Java backend experience with Spring Boot.",
                        "used_resume": "java-backend",
                    }
                )
            ),
            _chat_response(
                json.dumps(
                    {
                        "language": "en",
                        "cover_letter": "I have six years of backend experience with Java and Spring Boot.",
                        "used_resume": "java-backend",
                    }
                )
            ),
        ]
    )
    client = LLMClient(api_url="https://llm.local", api_key="secret", model="test-model")
    with pytest.raises(CoverLetterValidationError):
        client.create_cover_letter(
            prompt="PROMPT",
            candidate_profile="Java Backend Engineer with 7 years of commercial backend experience.",
            vacancy_text="Content completeness: PARTIAL",
            analysis=_evaluation(),
            recommended_resume="java-backend",
        )
    assert route.call_count == 2


@respx.mock
def test_lead_vacancy_title_not_repeated_as_candidate_positioning() -> None:
    respx.post("https://llm.local/chat/completions").mock(
        side_effect=[
            _chat_response(
                json.dumps(
                    {
                        "language": "en",
                        "cover_letter": (
                            "I have around seven years of backend experience and I am interested in the Backend Lead position."
                        ),
                        "used_resume": "java-backend",
                    }
                )
            ),
            _chat_response(
                json.dumps(
                    {
                        "language": "en",
                        "cover_letter": (
                            "I am a Java Backend Engineer with around seven years of experience with Java, Spring Boot and Kafka. "
                            "I have worked with microservices and distributed integrations. "
                            "My background is relevant to this Java/Kotlin backend role."
                        ),
                        "used_resume": "java-backend",
                    }
                )
            ),
        ]
    )
    client = LLMClient(api_url="https://llm.local", api_key="secret", model="test-model")
    result = client.create_cover_letter(
        prompt="PROMPT",
        candidate_profile="Java Backend Engineer with 7 years of commercial backend experience.",
        vacancy_text="Title: Backend Lead (Java/Kotlin)\nContent completeness: PARTIAL",
        analysis=_evaluation(),
        recommended_resume="java-backend",
    )
    assert "backend lead position" not in result.cover_letter.lower()


@respx.mock
def test_incomplete_vacancy_requires_neutral_wording_and_no_management_claims() -> None:
    respx.post("https://llm.local/chat/completions").mock(
        side_effect=[
            _chat_response(
                json.dumps(
                    {
                        "language": "en",
                        "cover_letter": (
                            "I have around seven years of Java backend experience and strong alignment with your requirements. "
                            "I also have team leadership and architecture ownership experience."
                        ),
                        "used_resume": "java-backend",
                    }
                )
            ),
            _chat_response(
                json.dumps(
                    {
                        "language": "en",
                        "cover_letter": (
                            "I am a Java Backend Engineer with around seven years of experience with Java, Spring Boot and Kotlin. "
                            "My experience includes microservices and production integrations. "
                            "My background is relevant to this backend engineering role."
                        ),
                        "used_resume": "java-backend",
                    }
                )
            ),
        ]
    )
    client = LLMClient(api_url="https://llm.local", api_key="secret", model="test-model")
    result = client.create_cover_letter(
        prompt="PROMPT",
        candidate_profile="Java Backend Engineer with 7 years of commercial backend experience.",
        vacancy_text="Title: Backend Lead\nContent completeness: PARTIAL",
        analysis=_evaluation(),
        recommended_resume="java-backend",
    )
    lower = result.cover_letter.lower()
    assert "strong alignment" not in lower
    assert "team leadership" not in lower
    assert "architecture ownership" not in lower
    assert len(result.cover_letter.split()) <= 80


@respx.mock
def test_english_vacancy_prefers_english_language() -> None:
    respx.post("https://llm.local/chat/completions").mock(
        return_value=_chat_response(
            json.dumps(
                {
                    "language": "en",
                    "cover_letter": _VALID_EN_SUMMARY,
                    "used_resume": "java-backend",
                }
            )
        )
    )
    client = LLMClient(api_url="https://llm.local", api_key="secret", model="test-model")
    result = client.create_cover_letter(
        prompt="PROMPT",
        candidate_profile="Java Backend Engineer with seven years of experience.",
        vacancy_text="Title: Java Backend Engineer\nResponsibilities: Build backend services for payments.",
        analysis=_evaluation(),
        recommended_resume="java-backend",
        preferred_language="ru",
        grammatical_gender="female",
    )
    assert result.language == "en"


@respx.mock
def test_russian_vacancy_prefers_russian_language() -> None:
    respx.post("https://llm.local/chat/completions").mock(
        return_value=_chat_response(
            json.dumps(
                {
                    "language": "ru",
                    "cover_letter": (
                        "Здравствуйте! У меня около семи лет опыта backend-разработки на Java и Spring Boot. "
                        "Мой опыт включает микросервисы и Kafka в production-сервисах. "
                        "Буду рада обсудить технические ожидания по этой позиции."
                    ),
                    "used_resume": "java-backend",
                }
            )
        )
    )
    client = LLMClient(api_url="https://llm.local", api_key="secret", model="test-model")
    result = client.create_cover_letter(
        prompt="PROMPT",
        candidate_profile="Java Backend Engineer с около семи лет опыта.",
        vacancy_text="Вакансия: Java Backend Engineer\nОписание: разработка backend-сервисов.",
        analysis=_evaluation(),
        recommended_resume="java-backend",
        preferred_language="en",
        grammatical_gender="female",
    )
    assert result.language == "ru"


@respx.mock
def test_female_profile_rejects_masculine_verbs() -> None:
    respx.post("https://llm.local/chat/completions").mock(
        side_effect=[
            _chat_response(
                json.dumps(
                    {
                        "language": "ru",
                        "cover_letter": (
                            "Здравствуйте! У меня около семи лет опыта backend-разработки. "
                            "Я работал с Java и Spring Boot в production-сервисах."
                        ),
                        "used_resume": "java-backend",
                    }
                )
            ),
            _chat_response(
                json.dumps(
                    {
                        "language": "ru",
                        "cover_letter": (
                            "Здравствуйте! У меня около семи лет опыта backend-разработки на Java и Spring Boot. "
                            "Я работала с микросервисами и Kafka в production-сервисах."
                        ),
                        "used_resume": "java-backend",
                    }
                )
            ),
        ]
    )
    client = LLMClient(api_url="https://llm.local", api_key="secret", model="test-model")
    result = client.create_cover_letter(
        prompt="PROMPT",
        candidate_profile="Java Backend Engineer with 7 years.",
        vacancy_text="Вакансия: Java Backend Engineer",
        analysis=_evaluation(),
        recommended_resume="java-backend",
        preferred_language="ru",
        grammatical_gender="female",
    )
    assert "работал " not in result.cover_letter.lower()


@respx.mock
def test_repeat_runs_are_identical() -> None:
    payload = json.dumps(
        {
            "language": "en",
            "cover_letter": _VALID_EN_SUMMARY,
            "used_resume": "java-backend",
        }
    )
    respx.post("https://llm.local/chat/completions").mock(
        side_effect=[_chat_response(payload), _chat_response(payload)]
    )
    client = LLMClient(api_url="https://llm.local", api_key="secret", model="test-model")
    args = dict(
        prompt="PROMPT",
        candidate_profile="Java Backend Engineer with seven years of experience.",
        vacancy_text="Title: Java Backend Engineer",
        analysis=_evaluation(),
        recommended_resume="java-backend",
        preferred_language="en",
        grammatical_gender="neutral",
    )
    first = client.create_cover_letter(**args)
    second = client.create_cover_letter(**args)
    assert first == second


@respx.mock
def test_english_cover_letter_soft_generic_phrases_cleaned() -> None:
    route = respx.post("https://llm.local/chat/completions").mock(
        return_value=_chat_response(
            json.dumps(
                {
                    "language": "en",
                    "cover_letter": (
                        "I am a Java Backend Engineer with approximately seven years of experience with Java and Spring Boot. "
                        "I have built and maintained production services and integrations. "
                        "I work with distributed systems under practical delivery constraints. "
                        "This role aligns with my skills and fits your needs."
                    ),
                    "used_resume": "java-backend",
                }
            )
        )
    )
    client = LLMClient(api_url="https://llm.local", api_key="secret", model="test-model")
    result = client.create_cover_letter(
        prompt="PROMPT",
        candidate_profile="Java Backend Engineer with approximately seven years of commercial backend experience.",
        vacancy_text="Title: Java Backend Engineer",
        analysis=_evaluation(),
        recommended_resume="java-backend",
        preferred_language="en",
        grammatical_gender="neutral",
    )
    lower = result.cover_letter.lower()
    assert "aligns with my skills" not in lower
    assert "fits your needs" not in lower
    assert 3 <= _count_complete_sentences(result.cover_letter) <= 5
    assert route.call_count == 1


@respx.mock
def test_soft_phrase_open_to_discussing_cleaned_and_accepted(caplog: pytest.LogCaptureFixture) -> None:
    route = respx.post("https://llm.local/chat/completions").mock(
        return_value=_chat_response(
            json.dumps(
                {
                    "language": "en",
                    "cover_letter": (
                        "I am a Java Backend Engineer with around seven years of experience with Java and Spring Boot. "
                        "I have built and maintained production services and integrations. "
                        "My background is relevant to this Java Backend Engineer role. "
                        "I am open to discussing how my background fits your needs."
                    ),
                    "used_resume": "java-backend",
                }
            )
        )
    )
    client = LLMClient(api_url="https://llm.local", api_key="secret", model="test-model")
    with caplog.at_level(logging.INFO):
        result = client.create_cover_letter(
            prompt="PROMPT",
            candidate_profile="Java Backend Engineer with around seven years of commercial backend experience.",
            vacancy_text="Title: Java Backend Engineer",
            analysis=_evaluation(),
            recommended_resume="java-backend",
        )

    assert "i am open to discussing" not in result.cover_letter.lower()
    assert "fits your needs" not in result.cover_letter.lower()
    assert 3 <= _count_complete_sentences(result.cover_letter) <= 5
    assert route.call_count == 1
    assert "Cleaned" in "\n".join(record.getMessage() for record in caplog.records)


@respx.mock
def test_multiple_soft_phrases_cleaned_in_single_pass() -> None:
    route = respx.post("https://llm.local/chat/completions").mock(
        return_value=_chat_response(
            json.dumps(
                {
                    "language": "en",
                    "cover_letter": (
                        "I am a Java Backend Engineer with approximately seven years of experience with Java and Spring Boot. "
                        "I have built and maintained production services and integrations. "
                        "My background is relevant to this Java Backend Engineer role. "
                        "I am excited to apply and I believe this role aligns with my skills. "
                        "I would like to contribute my expertise to your dynamic team."
                    ),
                    "used_resume": "java-backend",
                }
            )
        )
    )
    client = LLMClient(api_url="https://llm.local", api_key="secret", model="test-model")
    result = client.create_cover_letter(
        prompt="PROMPT",
        candidate_profile="Java Backend Engineer with approximately seven years of commercial backend experience.",
        vacancy_text="Title: Java Backend Engineer",
        analysis=_evaluation(),
        recommended_resume="java-backend",
    )
    lower = result.cover_letter.lower()
    assert "i am excited" not in lower
    assert "i believe" not in lower
    assert "aligns with my skills" not in lower
    assert "i would like to contribute" not in lower
    assert "dynamic team" not in lower
    assert 3 <= _count_complete_sentences(result.cover_letter) <= 5
    assert route.call_count == 1
    assert len(result.cover_letter.split()) <= 80


@respx.mock
def test_summary_strips_how_my_experience_instruction_leak() -> None:
    route = respx.post("https://llm.local/chat/completions").mock(
        return_value=_chat_response(
            json.dumps(
                {
                    "language": "en",
                    "cover_letter": (
                        "I am a Java Backend Engineer with around seven years of experience with Java, Spring Boot, and REST APIs. "
                        "I have worked on production services and microservices with a focus on reliable integrations. "
                        "My background fits this Java Backend Engineer role. "
                        "how my experience is relevant to this role."
                    ),
                    "used_resume": "java-backend",
                }
            )
        )
    )
    client = LLMClient(api_url="https://llm.local", api_key="secret", model="test-model")
    result = client.create_cover_letter(
        prompt="PROMPT",
        candidate_profile="Java Backend Engineer with around seven years of commercial backend experience.",
        vacancy_text="Title: Java Backend Engineer",
        analysis=_evaluation(),
        recommended_resume="java-backend",
    )
    assert "how my experience" not in result.cover_letter.lower()
    for sentence in result.cover_letter.split("."):
        assert "how my experience" not in sentence.lower()
    assert route.call_count == 1


@respx.mock
def test_summary_strips_this_opportunity_aligns_starter() -> None:
    route = respx.post("https://llm.local/chat/completions").mock(
        return_value=_chat_response(
            json.dumps(
                {
                    "language": "en",
                    "cover_letter": (
                        "I am a Java Backend Engineer with around seven years of experience with Java and Spring Boot. "
                        "I have built and maintained production services and integrations. "
                        "My background focuses on distributed systems and practical delivery. "
                        "This opportunity aligns with my background in maintaining backend services."
                    ),
                    "used_resume": "java-backend",
                }
            )
        )
    )
    client = LLMClient(api_url="https://llm.local", api_key="secret", model="test-model")
    result = client.create_cover_letter(
        prompt="PROMPT",
        candidate_profile="Java Backend Engineer with around seven years of commercial backend experience.",
        vacancy_text="Title: Java Backend Engineer",
        analysis=_evaluation(),
        recommended_resume="java-backend",
    )
    lower = result.cover_letter.lower()
    assert "this opportunity aligns" not in lower
    for sentence in result.cover_letter.split("."):
        assert not sentence.strip().lower().startswith("this opportunity aligns")
    assert route.call_count == 1


@respx.mock
def test_summary_cleans_duplicated_backend_backend() -> None:
    route = respx.post("https://llm.local/chat/completions").mock(
        return_value=_chat_response(
            json.dumps(
                {
                    "language": "en",
                    "cover_letter": (
                        "I am a Java Backend Engineer with around seven years of experience with Java and Spring Boot. "
                        "I focus on backend backend systems and production integrations. "
                        "My background is relevant to this Java Backend Engineer role."
                    ),
                    "used_resume": "java-backend",
                }
            )
        )
    )
    client = LLMClient(api_url="https://llm.local", api_key="secret", model="test-model")
    result = client.create_cover_letter(
        prompt="PROMPT",
        candidate_profile="Java Backend Engineer with around seven years of commercial backend experience.",
        vacancy_text="Title: Java Backend Engineer",
        analysis=_evaluation(),
        recommended_resume="java-backend",
    )
    assert "backend backend" not in result.cover_letter.lower()
    assert 3 <= _count_complete_sentences(result.cover_letter) <= 5
    assert route.call_count == 1


def test_validate_rejects_duplicated_backend_backend() -> None:
    result = CoverLetterResult(
        language="en",
        cover_letter=(
            "I am a Java Backend Engineer with around seven years of experience with Java and Spring Boot. "
            "I focus on backend backend systems and production integrations. "
            "My background is relevant to this Java Backend Engineer role."
        ),
        used_resume="java-backend",
    )
    with pytest.raises(CoverLetterValidationError, match="Duplicated 'backend backend'"):
        _validate_cover_letter(
            result=result,
            vacancy_text="Title: Java Backend Engineer",
            candidate_profile="Java Backend Engineer with around seven years of experience.",
            preferred_language="en",
            grammatical_gender="neutral",
        )


@respx.mock
def test_english_summary_requires_three_to_five_sentences() -> None:
    route = respx.post("https://llm.local/chat/completions").mock(
        side_effect=[
            _chat_response(
                json.dumps(
                    {
                        "language": "en",
                        "cover_letter": (
                            "I am a Java Backend Engineer with around seven years of experience with Java and Spring Boot. "
                            "My background is relevant to this Java Backend Engineer role."
                        ),
                        "used_resume": "java-backend",
                    }
                )
            ),
            _chat_response(
                json.dumps(
                    {
                        "language": "en",
                        "cover_letter": _VALID_EN_SUMMARY,
                        "used_resume": "java-backend",
                    }
                )
            ),
        ]
    )
    client = LLMClient(api_url="https://llm.local", api_key="secret", model="test-model")
    result = client.create_cover_letter(
        prompt="PROMPT",
        candidate_profile="Java Backend Engineer with around seven years of commercial backend experience.",
        vacancy_text="Title: Java Backend Engineer",
        analysis=_evaluation(),
        recommended_resume="java-backend",
    )
    assert 3 <= _count_complete_sentences(result.cover_letter) <= 5
    assert route.call_count == 2


def test_validate_rejects_how_my_experience_sentence() -> None:
    result = CoverLetterResult(
        language="en",
        cover_letter=(
            "I am a Java Backend Engineer with around seven years of experience with Java and Spring Boot. "
            "I have built production services and integrations. "
            "how my experience is relevant to this role."
        ),
        used_resume="java-backend",
    )
    with pytest.raises(CoverLetterValidationError, match="Prompt/instruction fragment"):
        _validate_cover_letter(
            result=result,
            vacancy_text="Title: Java Backend Engineer",
            candidate_profile="Java Backend Engineer with around seven years of experience.",
            preferred_language="en",
            grammatical_gender="neutral",
        )


def test_soft_cleanup_removes_opportunity_and_leak_sentences() -> None:
    raw = (
        "I am a Java Backend Engineer with around seven years of experience with Java and Spring Boot. "
        "I have built production services and integrations. "
        "My background focuses on distributed systems delivery. "
        "This opportunity aligns with my background in services. "
        "how my experience is relevant to this role."
    )
    cleaned, applied = _apply_soft_cover_letter_cleanup(raw)
    assert applied >= 2
    assert "how my experience" not in cleaned.lower()
    assert "this opportunity aligns" not in cleaned.lower()
    assert 3 <= _count_complete_sentences(cleaned) <= 5
