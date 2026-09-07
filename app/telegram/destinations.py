from __future__ import annotations

from enum import Enum
from typing import Protocol

from app.telegram.client import TelegramClient


class TelegramDestination(str, Enum):
    LINKEDIN = "linkedin"
    TARGET_COMPANIES = "target_companies"


class TelegramDestinationConfigError(ValueError):
    """Raised when a Telegram destination cannot be resolved to a chat id."""


class TelegramChatIdSettings(Protocol):
    bot_token: str
    chat_id: str
    linkedin_chat_id: str
    target_companies_chat_id: str


def resolve_telegram_chat_id(
    telegram: TelegramChatIdSettings,
    destination: TelegramDestination | str,
) -> str:
    resolved_destination = _coerce_destination(destination)
    if resolved_destination is TelegramDestination.LINKEDIN:
        return _first_chat_id(
            _attr_chat_id(telegram, "linkedin_chat_id"),
            _attr_chat_id(telegram, "chat_id"),
        )
    if resolved_destination is TelegramDestination.TARGET_COMPANIES:
        chat_id = _attr_chat_id(telegram, "target_companies_chat_id")
        if not chat_id:
            raise TelegramDestinationConfigError(
                "TELEGRAM__TARGET_COMPANIES_CHAT_ID is not set. "
                "Target Companies delivery does not fall back to the LinkedIn Telegram channel."
            )
        return chat_id
    raise TelegramDestinationConfigError(
        f"Unknown Telegram destination: {resolved_destination!r}."
    )


def configured_telegram_chat_ids(telegram: TelegramChatIdSettings) -> frozenset[str]:
    chat_ids: set[str] = set()
    linkedin_chat_id = resolve_telegram_chat_id(telegram, TelegramDestination.LINKEDIN)
    if linkedin_chat_id:
        chat_ids.add(linkedin_chat_id)
    target_chat_id = _attr_chat_id(telegram, "target_companies_chat_id")
    if target_chat_id:
        chat_ids.add(target_chat_id)
    return frozenset(chat_ids)


def telegram_client_for(
    telegram: TelegramChatIdSettings,
    destination: TelegramDestination | str,
) -> TelegramClient:
    return TelegramClient(
        bot_token=telegram.bot_token,
        chat_id=resolve_telegram_chat_id(telegram, destination),
    )


def _coerce_destination(destination: TelegramDestination | str) -> TelegramDestination:
    if isinstance(destination, TelegramDestination):
        return destination
    normalized = str(destination).strip().lower()
    try:
        return TelegramDestination(normalized)
    except ValueError as exc:
        raise TelegramDestinationConfigError(
            f"Unknown Telegram destination: {destination!r}. "
            "Expected 'linkedin' or 'target_companies'."
        ) from exc


def _attr_chat_id(telegram: object, name: str) -> str:
    return str(getattr(telegram, name, "") or "").strip()


def _first_chat_id(*values: str) -> str:
    for value in values:
        chat_id = str(value or "").strip()
        if chat_id:
            return chat_id
    return ""
