import json
import logging

import httpx
import pytest
import respx

from app.llm_client import LLMClient, LLMRequestError, LLMResponseError


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
