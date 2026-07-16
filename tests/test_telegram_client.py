import httpx
import pytest

from app.telegram.client import (
    TelegramClient,
    TelegramRequestError,
    build_action_buttons,
    build_prepared_application_buttons,
    map_code_to_source,
    map_source_to_code,
    parse_callback_data,
    validate_linkedin_job_url,
)
from app.telegram.models import TelegramVacancyCard


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    def __init__(self, responses: list[_FakeResponse], recorder: list[tuple[str, dict]]) -> None:
        self._responses = responses
        self._recorder = recorder

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def post(self, url: str, json: dict):
        self._recorder.append((url, json))
        return self._responses.pop(0)


def test_send_message_payload(monkeypatch) -> None:
    calls: list[tuple[str, dict]] = []
    responses = [
        _FakeResponse(
            200,
            {"ok": True, "result": {"message_id": 77, "chat": {"id": 123}}},
        )
    ]

    def fake_client(*args, **kwargs):
        _ = args, kwargs
        return _FakeClient(responses, calls)

    monkeypatch.setattr(httpx, "Client", fake_client)
    client = TelegramClient(bot_token="secret-token", chat_id="123")
    ref = client.send_vacancy_card(
        TelegramVacancyCard(
            source="li",
            external_id="4439013108",
            decision="POTENTIAL_MATCH",
            title="Java Backend",
            company="ACME",
            location="Remote",
            url="https://www.linkedin.com/jobs/view/4439013108/",
            match_percentage=None,
            gaps=[],
            nuances=[],
            recommended_resume="java-backend",
            content_completeness="PARTIAL",
        )
    )

    assert ref.message_id == 77
    assert ref.chat_id == "123"
    assert len(calls) == 1
    _, payload = calls[0]
    assert payload["parse_mode"] == "HTML"
    assert payload["chat_id"] == "123"
    assert payload["reply_markup"]["inline_keyboard"][1][0]["callback_data"] == "skip:li:4439013108"
    assert payload["reply_markup"]["inline_keyboard"][1][1]["callback_data"] == "prepare:li:4439013108"


def test_url_validation_and_callback_limit() -> None:
    assert validate_linkedin_job_url("https://www.linkedin.com/jobs/view/1/").endswith("/1/")
    with pytest.raises(ValueError):
        validate_linkedin_job_url("https://example.com/jobs/view/1/")

    buttons = build_action_buttons("linkedin-email", "4439013108", "https://www.linkedin.com/jobs/view/4439013108/")
    assert len(buttons) == 2
    assert map_source_to_code("linkedin-email") == "li"
    assert map_code_to_source("li") == "linkedin-email"
    assert parse_callback_data("skip:li:4439013108") == ("skip", "linkedin-email", "4439013108")
    assert parse_callback_data("applied:li:4439013108") == ("applied", "linkedin-email", "4439013108")
    with pytest.raises(ValueError):
        parse_callback_data("bad:data")

    prepared_buttons = build_prepared_application_buttons(
        "linkedin-email",
        "4439013108",
        "https://www.linkedin.com/jobs/view/4439013108/",
    )
    assert prepared_buttons[0][0].callback_data == "applied:li:4439013108"
    assert prepared_buttons[0][1].callback_data == "skip:li:4439013108"
    assert prepared_buttons[1][0].url == "https://www.linkedin.com/jobs/view/4439013108/"


def test_send_prepared_application_payload_contains_buttons(monkeypatch) -> None:
    calls: list[tuple[str, dict]] = []
    responses = [
        _FakeResponse(
            200,
            {"ok": True, "result": {"message_id": 88, "chat": {"id": 123}}},
        )
    ]

    def fake_client(*args, **kwargs):
        _ = args, kwargs
        return _FakeClient(responses, calls)

    monkeypatch.setattr(httpx, "Client", fake_client)
    client = TelegramClient(bot_token="secret-token", chat_id="123")
    ref = client.send_prepared_application(
        source="linkedin-email",
        external_id="4439013108",
        title="Backend Lead (Java/Kotlin)",
        company="Salmon Group Ltd",
        language="en",
        recommended_resume="java-backend",
        cover_letter="Short cover letter.",
        warnings=["Описание вакансии неполное — требуется открыть LinkedIn"],
        url="https://www.linkedin.com/jobs/view/4439013108/",
    )

    assert ref.message_id == 88
    _, payload = calls[0]
    keyboard = payload["reply_markup"]["inline_keyboard"]
    assert keyboard[0][0]["callback_data"] == "applied:li:4439013108"
    assert keyboard[0][1]["callback_data"] == "skip:li:4439013108"
    assert keyboard[1][0]["url"] == "https://www.linkedin.com/jobs/view/4439013108/"


def test_telegram_error_does_not_leak_token(monkeypatch) -> None:
    responses = [_FakeResponse(500, {"ok": False, "description": "bad"})]

    def fake_client(*args, **kwargs):
        _ = args, kwargs
        return _FakeClient(responses, [])

    monkeypatch.setattr(httpx, "Client", fake_client)
    client = TelegramClient(bot_token="my-secret-token", chat_id="1")
    with pytest.raises(TelegramRequestError) as exc:
        client.get_updates(offset=None, timeout=1)
    assert "my-secret-token" not in str(exc.value)
