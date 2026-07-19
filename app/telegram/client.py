from __future__ import annotations

import httpx
from pathlib import Path

from app.telegram.formatter import (
    format_application_ready_card_html,
    format_prepared_application_html,
    format_preparing_application_html,
    format_telegram_card_html,
)
from app.telegram.models import TelegramDocumentRef, TelegramInlineButton, TelegramMessageRef, TelegramVacancyCard


class TelegramRequestError(Exception):
    """Raised when Telegram API request fails."""


class TelegramMessageNotModifiedError(TelegramRequestError):
    """Raised when Telegram edit has no changes."""


class TelegramClient:
    def __init__(self, bot_token: str, chat_id: str) -> None:
        self._bot_token = bot_token
        self._chat_id = str(chat_id)
        self._base_url = f"https://api.telegram.org/bot{bot_token}"

    def send_vacancy_card(self, card: TelegramVacancyCard) -> TelegramMessageRef:
        text = format_telegram_card_html(card)
        payload = {
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "reply_markup": {
                "inline_keyboard": _serialize_buttons(
                    build_action_buttons(source=card.source, external_id=card.external_id, url=card.url)
                )
            },
        }
        data = self._post_json("sendMessage", payload=payload, read_timeout=15.0)
        result = data.get("result", {})
        return TelegramMessageRef(
            chat_id=str(result.get("chat", {}).get("id", self._chat_id)),
            message_id=int(result.get("message_id", 0)),
        )

    def answer_callback_query(self, callback_query_id: str, text: str | None = None) -> None:
        payload = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        self._post_json("answerCallbackQuery", payload=payload, read_timeout=15.0)

    def edit_message_reply_markup(
        self,
        chat_id: str,
        message_id: int,
        buttons: list[list[TelegramInlineButton]],
    ) -> None:
        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
            "reply_markup": {"inline_keyboard": _serialize_buttons(buttons)},
        }
        self._post_json("editMessageReplyMarkup", payload=payload, read_timeout=15.0)

    def edit_message_text(
        self,
        *,
        chat_id: str,
        message_id: int,
        text: str,
        buttons: list[list[TelegramInlineButton]] | None = None,
        parse_mode: str | None = "HTML",
    ) -> None:
        payload: dict[str, object] = {
            "chat_id": str(chat_id),
            "message_id": int(message_id),
            "text": text,
            "disable_web_page_preview": True,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if buttons is not None:
            payload["reply_markup"] = {"inline_keyboard": _serialize_buttons(buttons)}
        self._post_json("editMessageText", payload=payload, read_timeout=15.0)

    def get_updates(self, offset: int | None, timeout: int = 25) -> list[dict]:
        payload: dict[str, int] = {"timeout": timeout}
        if offset is not None:
            payload["offset"] = offset
        data = self._post_json("getUpdates", payload=payload, read_timeout=35.0)
        updates = data.get("result")
        if isinstance(updates, list):
            return [item for item in updates if isinstance(item, dict)]
        return []

    def send_text_message(
        self,
        text: str,
        *,
        chat_id: str | None = None,
        reply_to_message_id: int | None = None,
    ) -> TelegramMessageRef:
        payload: dict[str, object] = {"chat_id": str(chat_id or self._chat_id), "text": text}
        if reply_to_message_id is not None and reply_to_message_id > 0:
            payload["reply_to_message_id"] = int(reply_to_message_id)
        data = self._post_json("sendMessage", payload=payload, read_timeout=15.0)
        result = data.get("result", {})
        return TelegramMessageRef(
            chat_id=str(result.get("chat", {}).get("id", chat_id or self._chat_id)),
            message_id=int(result.get("message_id", 0)),
        )

    def send_prepared_application(
        self,
        *,
        source: str,
        external_id: str,
        title: str,
        company: str | None,
        language: str,
        recommended_resume: str,
        cover_letter: str,
        warnings: list[str],
        url: str,
    ) -> TelegramMessageRef:
        text = format_prepared_application_html(
            title=title,
            company=company,
            language=language,
            recommended_resume=recommended_resume,
            cover_letter=cover_letter,
            warnings=warnings,
        )
        payload = {
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "reply_markup": {
                "inline_keyboard": _serialize_buttons(
                    build_prepared_application_buttons(source=source, external_id=external_id, url=url)
                )
            },
        }
        data = self._post_json("sendMessage", payload=payload, read_timeout=15.0)
        result = data.get("result", {})
        return TelegramMessageRef(
            chat_id=str(result.get("chat", {}).get("id", self._chat_id)),
            message_id=int(result.get("message_id", 0)),
        )

    def send_document(
        self,
        *,
        file_path: str,
        caption: str,
        chat_id: str | None = None,
        reply_to_message_id: int | None = None,
    ) -> TelegramDocumentRef:
        timeout = httpx.Timeout(connect=5.0, read=15.0, write=20.0, pool=5.0)
        url = f"{self._base_url}/sendDocument"
        try:
            with open(file_path, "rb") as handle:
                with httpx.Client(timeout=timeout) as client:
                    data: dict[str, object] = {"chat_id": str(chat_id or self._chat_id), "caption": caption}
                    if reply_to_message_id is not None and reply_to_message_id > 0:
                        data["reply_to_message_id"] = int(reply_to_message_id)
                    response = client.post(
                        url,
                        data=data,
                        files={"document": (Path(file_path).name, handle, "application/pdf")},
                    )
        except OSError as exc:
            raise TelegramRequestError("Telegram sendDocument file read failed.") from exc
        except httpx.TimeoutException as exc:
            raise TelegramRequestError("Telegram sendDocument timeout.") from exc
        except httpx.HTTPError as exc:
            raise TelegramRequestError("Telegram sendDocument request failed.") from exc

        if response.status_code >= 400:
            raise TelegramRequestError(f"Telegram sendDocument HTTP {response.status_code}.")
        try:
            data = response.json()
        except ValueError as exc:
            raise TelegramRequestError("Telegram sendDocument invalid JSON response.") from exc
        if not data.get("ok", False):
            raise TelegramRequestError(f"Telegram sendDocument API error (HTTP {response.status_code}).")
        return self._extract_document_ref(data=data)

    def send_document_by_file_id(
        self,
        *,
        chat_id: str,
        file_id: str,
        caption: str | None = None,
        reply_to_message_id: int | None = None,
    ) -> TelegramDocumentRef:
        payload: dict[str, object] = {"chat_id": str(chat_id), "document": file_id}
        if caption:
            payload["caption"] = caption
        if reply_to_message_id is not None and reply_to_message_id > 0:
            payload["reply_to_message_id"] = int(reply_to_message_id)
        data = self._post_json("sendDocument", payload=payload, read_timeout=15.0)
        return self._extract_document_ref(data=data)

    def delete_message(self, *, chat_id: str, message_id: int) -> None:
        payload = {"chat_id": str(chat_id), "message_id": int(message_id)}
        self._post_json("deleteMessage", payload=payload, read_timeout=15.0)

    def _post_json(self, endpoint: str, payload: dict, read_timeout: float) -> dict:
        timeout = httpx.Timeout(connect=5.0, read=read_timeout, write=10.0, pool=5.0)
        url = f"{self._base_url}/{endpoint}"
        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.post(url, json=payload)
        except httpx.TimeoutException as exc:
            raise TelegramRequestError(f"Telegram {endpoint} timeout.") from exc
        except httpx.HTTPError as exc:
            raise TelegramRequestError(f"Telegram {endpoint} request failed.") from exc

        if response.status_code >= 400:
            raise TelegramRequestError(f"Telegram {endpoint} HTTP {response.status_code}.")
        try:
            data = response.json()
        except ValueError as exc:
            raise TelegramRequestError(f"Telegram {endpoint} invalid JSON response.") from exc
        if not data.get("ok", False):
            description = str(data.get("description", ""))
            lowered = description.lower()
            if "message is not modified" in lowered:
                raise TelegramMessageNotModifiedError("Telegram message is not modified.")
            raise TelegramRequestError(f"Telegram {endpoint} API error (HTTP {response.status_code}).")
        return data

    def _extract_document_ref(self, *, data: dict) -> TelegramDocumentRef:
        result = data.get("result", {})
        document = result.get("document", {}) if isinstance(result, dict) else {}
        file_id = document.get("file_id") if isinstance(document, dict) else None
        file_unique_id = document.get("file_unique_id") if isinstance(document, dict) else None
        if not isinstance(file_id, str) or not file_id.strip():
            raise TelegramRequestError("Telegram sendDocument response missing file_id.")
        return TelegramDocumentRef(
            chat_id=str(result.get("chat", {}).get("id", self._chat_id)),
            message_id=int(result.get("message_id", 0)),
            file_id=file_id,
            file_unique_id=str(file_unique_id) if isinstance(file_unique_id, str) else None,
        )


def build_action_buttons(source: str, external_id: str, url: str) -> list[list[TelegramInlineButton]]:
    validated_url = validate_vacancy_url(url)
    compact_source = map_source_to_code(source)
    skip_data = _callback_data("skip", compact_source, external_id)
    prepare_data = _callback_data("prepare", compact_source, external_id)
    return [
        [
            TelegramInlineButton(text="Prepare application", callback_data=prepare_data),
            TelegramInlineButton(text="Skip", callback_data=skip_data),
        ],
        [TelegramInlineButton(text="Open vacancy", url=validated_url)],
    ]


def build_prepared_application_buttons(source: str, external_id: str, url: str) -> list[list[TelegramInlineButton]]:
    validated_url = validate_vacancy_url(url)
    compact_source = map_source_to_code(source)
    copy_data = _callback_data("copy", compact_source, external_id)
    resume_data = _callback_data("resume", compact_source, external_id)
    applied_data = _callback_data("applied", compact_source, external_id)
    skip_data = _callback_data("skip", compact_source, external_id)
    return [
        [TelegramInlineButton(text="📋 Copy Cover Letter", callback_data=copy_data)],
        [TelegramInlineButton(text="📎 Resume PDF", callback_data=resume_data)],
        [TelegramInlineButton(text="🔗 Open vacancy", url=validated_url)],
        [
            TelegramInlineButton(text="✅ Applied", callback_data=applied_data),
            TelegramInlineButton(text="❌ Skip", callback_data=skip_data),
        ],
    ]


def build_loading_buttons(url: str) -> list[list[TelegramInlineButton]]:
    validated_url = validate_vacancy_url(url)
    return [[TelegramInlineButton(text="🔗 Open vacancy", url=validated_url)]]


def build_archived_buttons(url: str) -> list[list[TelegramInlineButton]]:
    validated_url = validate_vacancy_url(url)
    return [[TelegramInlineButton(text="🔗 Open vacancy", url=validated_url)]]


def build_prepare_failed_buttons(source: str, external_id: str, url: str) -> list[list[TelegramInlineButton]]:
    validated_url = validate_vacancy_url(url)
    compact_source = map_source_to_code(source)
    prepare_data = _callback_data("prepare", compact_source, external_id)
    skip_data = _callback_data("skip", compact_source, external_id)
    return [
        [TelegramInlineButton(text="Retry preparation", callback_data=prepare_data)],
        [TelegramInlineButton(text="Skip", callback_data=skip_data)],
        [TelegramInlineButton(text="Open vacancy", url=validated_url)],
    ]


def build_loading_text(*, title: str, company: str | None) -> str:
    return format_preparing_application_html(title=title, company=company)


def build_ready_text(*, title: str, company: str | None, recommended_resume: str) -> str:
    return format_application_ready_card_html(
        title=title,
        company=company,
        recommended_resume=recommended_resume,
    )


def map_source_to_code(source: str) -> str:
    mapping = {"linkedin-email": "li", "li": "li", "greenhouse": "gh", "gh": "gh"}
    mapped = mapping.get(source)
    if mapped is None:
        raise ValueError(f"Unknown source: {source}")
    return mapped


def map_code_to_source(code: str) -> str:
    reverse = {
        "li": "linkedin-email",
        "linkedin-email": "linkedin-email",
        "gh": "greenhouse",
        "greenhouse": "greenhouse",
    }
    mapped = reverse.get(code)
    if mapped is None:
        raise ValueError(f"Unknown source code: {code}")
    return mapped


def parse_callback_data(value: str) -> tuple[str, str, str]:
    parts = value.split(":")
    if len(parts) != 3:
        raise ValueError("Malformed callback data.")
    action, source_code, external_id = parts
    if action not in {"skip", "prepare", "applied", "copy", "resume"}:
        raise ValueError("Unsupported callback action.")
    if not external_id.strip():
        raise ValueError("Invalid external id in callback data.")
    source = map_code_to_source(source_code)
    return action, source, external_id


def validate_linkedin_job_url(url: str) -> str:
    return validate_vacancy_url(url)


def validate_vacancy_url(url: str) -> str:
    normalized = url.strip()
    if normalized.startswith("https://") or normalized.startswith("http://"):
        return normalized
    raise ValueError("Only HTTP(S) vacancy URLs are allowed.")


def _callback_data(action: str, source_code: str, external_id: str) -> str:
    value = f"{action}:{source_code}:{external_id}"
    if len(value.encode("utf-8")) > 64:
        raise ValueError("callback_data exceeds Telegram limit.")
    return value


def _serialize_buttons(buttons: list[list[TelegramInlineButton]]) -> list[list[dict[str, str]]]:
    result: list[list[dict[str, str]]] = []
    for row in buttons:
        serialized_row: list[dict[str, str]] = []
        for button in row:
            item = {"text": button.text}
            if button.url:
                item["url"] = button.url
            if button.callback_data:
                item["callback_data"] = button.callback_data
            serialized_row.append(item)
        result.append(serialized_row)
    return result
