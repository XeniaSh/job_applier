import httpx
import pytest

from app.telegram.client import (
    TelegramMessageNotModifiedError,
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

    def post(self, url: str, **kwargs):
        self._recorder.append((url, kwargs))
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
    _, request_kwargs = calls[0]
    payload = request_kwargs["json"]
    assert payload["parse_mode"] == "HTML"
    assert payload["chat_id"] == "123"
    assert payload["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "prepare:li:4439013108"
    assert payload["reply_markup"]["inline_keyboard"][0][1]["callback_data"] == "skip:li:4439013108"


def test_url_validation_and_callback_limit() -> None:
    assert validate_linkedin_job_url("https://www.linkedin.com/jobs/view/1/").endswith("/1/")
    assert validate_linkedin_job_url("https://job-boards.greenhouse.io/notion/jobs/2").endswith("/2")
    assert validate_linkedin_job_url("https://example.com/jobs/view/1/").endswith("/1/")

    buttons = build_action_buttons("linkedin-email", "4439013108", "https://www.linkedin.com/jobs/view/4439013108/")
    assert len(buttons) == 2
    assert buttons[1][0].text == "Open vacancy"
    assert map_source_to_code("linkedin-email") == "li"
    assert map_source_to_code("greenhouse") == "gh"
    assert map_code_to_source("li") == "linkedin-email"
    assert map_code_to_source("gh") == "greenhouse"
    assert parse_callback_data("skip:li:4439013108") == ("skip", "linkedin-email", "4439013108")
    assert parse_callback_data("applied:li:4439013108") == ("applied", "linkedin-email", "4439013108")
    assert parse_callback_data("copy:li:4439013108") == ("copy", "linkedin-email", "4439013108")
    assert parse_callback_data("resume:li:4439013108") == ("resume", "linkedin-email", "4439013108")
    with pytest.raises(ValueError):
        parse_callback_data("bad:data")

    prepared_buttons = build_prepared_application_buttons(
        "linkedin-email",
        "4439013108",
        "https://www.linkedin.com/jobs/view/4439013108/",
    )
    assert prepared_buttons[0][0].callback_data == "copy:li:4439013108"
    assert prepared_buttons[1][0].callback_data == "resume:li:4439013108"
    assert prepared_buttons[2][0].url == "https://www.linkedin.com/jobs/view/4439013108/"
    assert prepared_buttons[2][0].text == "🔗 Open vacancy"
    assert prepared_buttons[3][0].callback_data == "applied:li:4439013108"
    assert prepared_buttons[3][1].callback_data == "skip:li:4439013108"


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
    _, request_kwargs = calls[0]
    payload = request_kwargs["json"]
    keyboard = payload["reply_markup"]["inline_keyboard"]
    assert keyboard[0][0]["callback_data"] == "copy:li:4439013108"
    assert keyboard[1][0]["callback_data"] == "resume:li:4439013108"
    assert keyboard[2][0]["url"] == "https://www.linkedin.com/jobs/view/4439013108/"
    assert keyboard[3][0]["callback_data"] == "applied:li:4439013108"
    assert keyboard[3][1]["callback_data"] == "skip:li:4439013108"


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


def test_send_document_parses_file_ids(monkeypatch, tmp_path) -> None:
    calls: list[tuple[str, dict]] = []
    responses = [
        _FakeResponse(
            200,
            {
                "ok": True,
                "result": {
                    "message_id": 99,
                    "chat": {"id": 123},
                    "document": {"file_id": "FILE_ID_1234567890", "file_unique_id": "UNIQ_1"},
                },
            },
        )
    ]

    def fake_client(*args, **kwargs):
        _ = args, kwargs
        return _FakeClient(responses, calls)

    monkeypatch.setattr(httpx, "Client", fake_client)
    path = tmp_path / "resume.pdf"
    path.write_bytes(b"%PDF test")

    client = TelegramClient(bot_token="token", chat_id="123")
    ref = client.send_document(file_path=str(path), caption="cap")
    assert ref.message_id == 99
    assert ref.file_id == "FILE_ID_1234567890"
    assert ref.file_unique_id == "UNIQ_1"
    _, request_kwargs = calls[0]
    assert request_kwargs["data"]["chat_id"] == "123"
    assert request_kwargs["data"]["caption"] == "cap"
    assert "files" in request_kwargs


def test_send_document_by_file_id_payload(monkeypatch) -> None:
    calls: list[tuple[str, dict]] = []
    responses = [
        _FakeResponse(
            200,
            {
                "ok": True,
                "result": {
                    "message_id": 100,
                    "chat": {"id": 123},
                    "document": {"file_id": "FILE_ID_ABC", "file_unique_id": "UNIQ_2"},
                },
            },
        )
    ]

    def fake_client(*args, **kwargs):
        _ = args, kwargs
        return _FakeClient(responses, calls)

    monkeypatch.setattr(httpx, "Client", fake_client)
    client = TelegramClient(bot_token="token", chat_id="123")
    ref = client.send_document_by_file_id(chat_id="321", file_id="FILE_ID_ABC", caption="resume", reply_to_message_id=77)
    assert ref.message_id == 100
    _, request_kwargs = calls[0]
    payload = request_kwargs["json"]
    assert payload["chat_id"] == "321"
    assert payload["document"] == "FILE_ID_ABC"
    assert payload["caption"] == "resume"
    assert payload["reply_to_message_id"] == 77


def test_edit_message_not_modified_error_is_typed(monkeypatch) -> None:
    calls: list[tuple[str, dict]] = []
    responses = [
        _FakeResponse(
            200,
            {"ok": False, "description": "Bad Request: message is not modified"},
        )
    ]

    def fake_client(*args, **kwargs):
        _ = args, kwargs
        return _FakeClient(responses, calls)

    monkeypatch.setattr(httpx, "Client", fake_client)
    client = TelegramClient(bot_token="token", chat_id="123")
    with pytest.raises(TelegramMessageNotModifiedError):
        client.edit_message_text(chat_id="123", message_id=1, text="same")
