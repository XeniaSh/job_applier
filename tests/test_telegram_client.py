import httpx
import pytest

from app.telegram.client import (
    APPLICATION_PREPARE_ACTION,
    APPLICATION_PREPARE_BUTTON_TEXT,
    TelegramMessageNotModifiedError,
    TelegramClient,
    TelegramRequestError,
    build_action_buttons,
    build_prepared_application_buttons,
    map_code_to_source,
    map_source_to_code,
    parse_callback_data,
    source_supports_application_prepare,
    source_supports_prepare,
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
    keyboard = payload["reply_markup"]["inline_keyboard"]
    assert keyboard[0][0]["callback_data"] == "prepare:li:4439013108"
    assert keyboard[1][0]["callback_data"] == "applied:li:4439013108"
    assert keyboard[2][0]["callback_data"] == "skip:li:4439013108"
    assert keyboard[3][0]["url"] == "https://www.linkedin.com/jobs/view/4439013108/"


def test_url_validation_and_callback_limit() -> None:
    assert validate_linkedin_job_url("https://www.linkedin.com/jobs/view/1/").endswith("/1/")
    assert validate_linkedin_job_url("https://job-boards.greenhouse.io/notion/jobs/2").endswith("/2")
    assert validate_linkedin_job_url("https://example.com/jobs/view/1/").endswith("/1/")

    buttons = build_action_buttons("linkedin-email", "4439013108", "https://www.linkedin.com/jobs/view/4439013108/")
    assert len(buttons) == 4
    assert [button.text for button in buttons[0]] == ["🛠 Prepare"]
    assert [button.text for button in buttons[1]] == ["✅ Applied"]
    assert [button.text for button in buttons[2]] == ["⏭ Skip"]
    assert buttons[0][0].callback_data == "prepare:li:4439013108"
    assert buttons[1][0].callback_data == "applied:li:4439013108"
    assert buttons[2][0].callback_data == "skip:li:4439013108"
    assert buttons[3][0].text == "🔗 Open vacancy"
    assert map_source_to_code("linkedin-email") == "li"
    assert map_source_to_code("greenhouse") == "gh"
    assert map_code_to_source("li") == "linkedin-email"
    assert map_code_to_source("gh") == "greenhouse"
    assert parse_callback_data("skip:li:4439013108") == ("skip", "linkedin-email", "4439013108", None)
    assert parse_callback_data("applied:li:4439013108") == ("applied", "linkedin-email", "4439013108", None)
    assert parse_callback_data("copy:li:4439013108") == ("copy", "linkedin-email", "4439013108", None)
    assert parse_callback_data("resume:li:4439013108") == ("resume", "linkedin-email", "4439013108", None)
    assert parse_callback_data("undo:li:4439013108:abc12345") == (
        "undo",
        "linkedin-email",
        "4439013108",
        "abc12345",
    )
    with pytest.raises(ValueError):
        parse_callback_data("bad:data")

    prepared_buttons = build_prepared_application_buttons(
        "linkedin-email",
        "4439013108",
        "https://www.linkedin.com/jobs/view/4439013108/",
    )
    assert prepared_buttons[0][0].callback_data == "copy:li:4439013108"
    assert prepared_buttons[0][0].text == "📋 Cover Letter"
    assert prepared_buttons[1][0].callback_data == "resume:li:4439013108"
    assert prepared_buttons[2][0].url == "https://www.linkedin.com/jobs/view/4439013108/"
    assert prepared_buttons[2][0].text == "🔗 Open vacancy"
    assert prepared_buttons[3][0].callback_data == "applied:li:4439013108"
    assert prepared_buttons[4][0].callback_data == "skip:li:4439013108"


def test_target_company_greenhouse_callback_round_trip() -> None:
    full_agoda = "target_company:greenhouse:agoda"
    full_jetbrains = "target_company:greenhouse:jetbrains"
    assert map_source_to_code(full_agoda) == "tcg.agoda"
    assert map_source_to_code("tcg.agoda") == "tcg.agoda"
    assert map_code_to_source("tcg.agoda") == full_agoda
    assert map_code_to_source(full_agoda) == full_agoda
    assert map_code_to_source(map_source_to_code(full_agoda)) == full_agoda
    assert map_source_to_code(map_code_to_source("tcg.jetbrains")) == "tcg.jetbrains"

    parsed_agoda = parse_callback_data("skip:tcg.agoda:12")
    parsed_jetbrains = parse_callback_data("applied:tcg.jetbrains:12")
    assert parsed_agoda == ("skip", full_agoda, "12", None)
    assert parsed_jetbrains == ("applied", full_jetbrains, "12", None)
    assert parsed_agoda[1] != parsed_jetbrains[1]
    assert parse_callback_data("undo:tcg.agoda:739281:abc12345") == (
        "undo",
        full_agoda,
        "739281",
        "abc12345",
    )
    assert parse_callback_data("prepapp:tcg.agoda:6886113") == (
        APPLICATION_PREPARE_ACTION,
        full_agoda,
        "6886113",
        None,
    )
    assert map_source_to_code("linkedin-email") == "li"
    assert map_source_to_code("greenhouse") == "gh"
    assert map_code_to_source("li") == "linkedin-email"
    assert map_code_to_source("gh") == "greenhouse"


def test_target_company_lever_callback_round_trip() -> None:
    full_loom = "target_company:lever:loom"
    full_notion = "target_company:lever:notion"
    assert map_source_to_code(full_loom) == "tcl.loom"
    assert map_source_to_code("tcl.loom") == "tcl.loom"
    assert map_code_to_source("tcl.loom") == full_loom
    assert map_code_to_source(full_loom) == full_loom
    assert map_code_to_source(map_source_to_code(full_loom)) == full_loom
    assert map_source_to_code(map_code_to_source("tcl.notion")) == "tcl.notion"

    parsed_loom = parse_callback_data("skip:tcl.loom:12")
    parsed_notion = parse_callback_data("applied:tcl.notion:12")
    assert parsed_loom == ("skip", full_loom, "12", None)
    assert parsed_notion == ("applied", full_notion, "12", None)
    assert parsed_loom[1] != parsed_notion[1]
    assert parse_callback_data("undo:tcl.loom:739281:abc12345") == (
        "undo",
        full_loom,
        "739281",
        "abc12345",
    )
    assert parse_callback_data("prepapp:tcl.loom:6886113") == (
        APPLICATION_PREPARE_ACTION,
        full_loom,
        "6886113",
        None,
    )

    # Greenhouse and Lever target-company codes never collide with each other.
    assert map_source_to_code(full_loom) != map_source_to_code("target_company:greenhouse:loom")


def test_prepare_is_hidden_for_target_companies_and_generic_greenhouse() -> None:
    url = "https://job-boards.greenhouse.io/agoda/jobs/739281"
    linkedin = build_action_buttons(
        "linkedin-email",
        "4439013108",
        "https://www.linkedin.com/jobs/view/4439013108/",
    )
    target_full = build_action_buttons("target_company:greenhouse:agoda", "739281", url)
    target_code = build_action_buttons("tcg.agoda", "739281", url)
    generic = build_action_buttons("greenhouse", "12", url)

    assert source_supports_prepare("linkedin-email") is True
    assert source_supports_prepare("li") is True
    assert source_supports_prepare("target_company:greenhouse:agoda") is False
    assert source_supports_prepare("tcg.agoda") is False
    assert source_supports_prepare("greenhouse") is False
    assert source_supports_prepare("gh") is False
    assert source_supports_application_prepare("target_company:greenhouse:agoda") is True
    assert source_supports_application_prepare("tcg.agoda") is True
    assert source_supports_application_prepare("linkedin-email") is False
    assert source_supports_application_prepare("greenhouse") is False
    assert source_supports_application_prepare("gh") is False

    assert [button.text for row in linkedin for button in row] == [
        "🛠 Prepare",
        "✅ Applied",
        "⏭ Skip",
        "🔗 Open vacancy",
    ]
    assert linkedin[0][0].callback_data == "prepare:li:4439013108"
    for buttons in (target_full, target_code):
        labels = [button.text for row in buttons for button in row]
        assert "🛠 Prepare" not in labels
        assert labels == [
            APPLICATION_PREPARE_BUTTON_TEXT,
            "✅ Applied",
            "⏭ Skip",
            "🔗 Open vacancy",
        ]
        assert buttons[0][0].callback_data == "prepapp:tcg.agoda:739281"
        assert buttons[1][0].callback_data == "applied:tcg.agoda:739281"
        assert buttons[2][0].callback_data == "skip:tcg.agoda:739281"
    generic_labels = [button.text for row in generic for button in row]
    assert "🛠 Prepare" not in generic_labels
    assert APPLICATION_PREPARE_BUTTON_TEXT not in generic_labels
    assert generic_labels == ["✅ Applied", "⏭ Skip", "🔗 Open vacancy"]
    assert generic[0][0].callback_data == "applied:gh:12"
    assert target_full[-1][0].url == url
    assert generic[-1][0].text == "🔗 Open vacancy"


def test_application_prepare_is_supported_for_target_company_lever() -> None:
    url = "https://jobs.lever.co/loom/739281"
    target_full = build_action_buttons("target_company:lever:loom", "739281", url)
    target_code = build_action_buttons("tcl.loom", "739281", url)

    assert source_supports_prepare("target_company:lever:loom") is False
    assert source_supports_prepare("tcl.loom") is False
    assert source_supports_application_prepare("target_company:lever:loom") is True
    assert source_supports_application_prepare("tcl.loom") is True

    for buttons in (target_full, target_code):
        labels = [button.text for row in buttons for button in row]
        assert "🛠 Prepare" not in labels
        assert labels == [
            APPLICATION_PREPARE_BUTTON_TEXT,
            "✅ Applied",
            "⏭ Skip",
            "🔗 Open vacancy",
        ]
        assert buttons[0][0].callback_data == "prepapp:tcl.loom:739281"
        assert buttons[1][0].callback_data == "applied:tcl.loom:739281"
        assert buttons[2][0].callback_data == "skip:tcl.loom:739281"
    assert target_full[-1][0].url == url


def test_target_company_ashby_callback_round_trip() -> None:
    full_perk = "target_company:ashby:perk"
    full_other = "target_company:ashby:other"
    assert map_source_to_code(full_perk) == "tca.perk"
    assert map_source_to_code("tca.perk") == "tca.perk"
    assert map_code_to_source("tca.perk") == full_perk
    assert map_code_to_source(full_perk) == full_perk
    assert map_code_to_source(map_source_to_code(full_perk)) == full_perk
    assert map_source_to_code(map_code_to_source("tca.other")) == "tca.other"

    parsed_perk = parse_callback_data("skip:tca.perk:12")
    parsed_other = parse_callback_data("applied:tca.other:12")
    assert parsed_perk == ("skip", full_perk, "12", None)
    assert parsed_other == ("applied", full_other, "12", None)
    assert parsed_perk[1] != parsed_other[1]
    assert parse_callback_data("undo:tca.perk:739281:abc12345") == (
        "undo",
        full_perk,
        "739281",
        "abc12345",
    )

    # Ashby codes never collide with Greenhouse or Lever target-company codes.
    assert map_source_to_code(full_perk) != map_source_to_code("target_company:greenhouse:perk")
    assert map_source_to_code(full_perk) != map_source_to_code("target_company:lever:perk")


def test_application_prepare_is_supported_for_target_company_ashby() -> None:
    # Ashby now has a bounded autofill adapter (AshbyAdapter) -- explicit
    # Prepare application must be offered, exactly like Greenhouse/Lever.
    url = "https://jobs.ashbyhq.com/perk/739281"
    target_full = build_action_buttons("target_company:ashby:perk", "739281", url)
    target_code = build_action_buttons("tca.perk", "739281", url)

    assert source_supports_prepare("target_company:ashby:perk") is False
    assert source_supports_prepare("tca.perk") is False
    assert source_supports_application_prepare("target_company:ashby:perk") is True
    assert source_supports_application_prepare("tca.perk") is True

    for buttons in (target_full, target_code):
        labels = [button.text for row in buttons for button in row]
        assert "🛠 Prepare" not in labels
        assert labels == [
            APPLICATION_PREPARE_BUTTON_TEXT,
            "✅ Applied",
            "⏭ Skip",
            "🔗 Open vacancy",
        ]
        assert buttons[0][0].callback_data == "prepapp:tca.perk:739281"
        assert buttons[1][0].callback_data == "applied:tca.perk:739281"
        assert buttons[2][0].callback_data == "skip:tca.perk:739281"
    assert target_full[-1][0].url == url


def test_application_prepare_is_not_supported_for_generic_ashby_source() -> None:
    # Only the explicit target_company:ashby:* namespace is wired to Prepare
    # -- a bare/generic "ashby" source string must never match.
    assert source_supports_application_prepare("ashby") is False
    assert source_supports_application_prepare("ashbyhq") is False


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
    assert keyboard[0][0]["text"] == "📋 Cover Letter"
    assert keyboard[1][0]["callback_data"] == "resume:li:4439013108"
    assert keyboard[2][0]["url"] == "https://www.linkedin.com/jobs/view/4439013108/"
    assert keyboard[3][0]["callback_data"] == "applied:li:4439013108"
    assert keyboard[4][0]["callback_data"] == "skip:li:4439013108"


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


def test_telegram_http_error_exposes_method_status_and_description(monkeypatch) -> None:
    calls: list[tuple[str, dict]] = []
    responses = [
        _FakeResponse(
            400,
            {"ok": False, "error_code": 400, "description": "Bad Request: query is too old"},
        )
    ]

    def fake_client(*args, **kwargs):
        _ = args, kwargs
        return _FakeClient(responses, calls)

    monkeypatch.setattr(httpx, "Client", fake_client)
    client = TelegramClient(bot_token="token", chat_id="123")
    with pytest.raises(TelegramRequestError) as exc:
        client.answer_callback_query("cb-id")
    err = exc.value
    assert err.method == "answerCallbackQuery"
    assert err.http_status == 400
    assert err.error_code == 400
    assert err.description == "Bad Request: query is too old"


def test_telegram_429_exposes_retry_after(monkeypatch) -> None:
    calls: list[tuple[str, dict]] = []
    responses = [
        _FakeResponse(
            429,
            {
                "ok": False,
                "error_code": 429,
                "description": "Too Many Requests: retry after 5",
                "parameters": {"retry_after": 5},
            },
        )
    ]

    def fake_client(*args, **kwargs):
        _ = args, kwargs
        return _FakeClient(responses, calls)

    monkeypatch.setattr(httpx, "Client", fake_client)
    client = TelegramClient(bot_token="token", chat_id="123")
    with pytest.raises(TelegramRequestError) as exc:
        client.answer_callback_query("cb-id")
    assert exc.value.error_code == 429
    assert exc.value.retry_after == 5.0


def test_telegram_error_without_parameters_has_no_retry_after(monkeypatch) -> None:
    calls: list[tuple[str, dict]] = []
    responses = [
        _FakeResponse(
            400,
            {"ok": False, "error_code": 400, "description": "Bad Request: query is too old"},
        )
    ]

    def fake_client(*args, **kwargs):
        _ = args, kwargs
        return _FakeClient(responses, calls)

    monkeypatch.setattr(httpx, "Client", fake_client)
    client = TelegramClient(bot_token="token", chat_id="123")
    with pytest.raises(TelegramRequestError) as exc:
        client.answer_callback_query("cb-id")
    assert exc.value.retry_after is None
