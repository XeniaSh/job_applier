import pytest

from app.config import Settings, TelegramSettings
from app.telegram.destinations import (
    TelegramDestination,
    TelegramDestinationConfigError,
    configured_telegram_chat_ids,
    resolve_telegram_chat_id,
    telegram_client_for,
)


def _set_llm_env(monkeypatch) -> None:
    monkeypatch.setenv("LLM_API_URL", "https://llm.local")
    monkeypatch.setenv("LLM_API_KEY", "secret")
    monkeypatch.setenv("LLM_MODEL", "model")


def test_linkedin_uses_linkedin_chat_id(monkeypatch) -> None:
    _set_llm_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM__BOT_TOKEN", "telegram-token")
    monkeypatch.setenv("TELEGRAM__LINKEDIN_CHAT_ID", "111")
    monkeypatch.setenv("TELEGRAM__TARGET_COMPANIES_CHAT_ID", "222")
    monkeypatch.delenv("TELEGRAM__CHAT_ID", raising=False)

    settings = Settings()
    assert resolve_telegram_chat_id(settings.telegram, TelegramDestination.LINKEDIN) == "111"
    client = telegram_client_for(settings.telegram, TelegramDestination.LINKEDIN)
    assert client._chat_id == "111"


def test_target_companies_uses_target_companies_chat_id(monkeypatch) -> None:
    _set_llm_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM__BOT_TOKEN", "telegram-token")
    monkeypatch.setenv("TELEGRAM__CHAT_ID", "legacy")
    monkeypatch.setenv("TELEGRAM__LINKEDIN_CHAT_ID", "111")
    monkeypatch.setenv("TELEGRAM__TARGET_COMPANIES_CHAT_ID", "222")

    settings = Settings()
    assert resolve_telegram_chat_id(settings.telegram, TelegramDestination.TARGET_COMPANIES) == "222"
    client = telegram_client_for(settings.telegram, TelegramDestination.TARGET_COMPANIES)
    assert client._chat_id == "222"


def test_legacy_chat_id_is_linkedin_fallback(monkeypatch) -> None:
    _set_llm_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM__BOT_TOKEN", "telegram-token")
    monkeypatch.setenv("TELEGRAM__CHAT_ID", "legacy")
    monkeypatch.delenv("TELEGRAM__LINKEDIN_CHAT_ID", raising=False)
    monkeypatch.delenv("TELEGRAM__TARGET_COMPANIES_CHAT_ID", raising=False)

    settings = Settings()
    assert settings.telegram.chat_id == "legacy"
    assert resolve_telegram_chat_id(settings.telegram, TelegramDestination.LINKEDIN) == "legacy"
    assert resolve_telegram_chat_id(settings.telegram, "linkedin") == "legacy"


def test_explicit_linkedin_chat_id_wins_over_legacy(monkeypatch) -> None:
    _set_llm_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM__BOT_TOKEN", "telegram-token")
    monkeypatch.setenv("TELEGRAM__CHAT_ID", "legacy")
    monkeypatch.setenv("TELEGRAM__LINKEDIN_CHAT_ID", "111")

    settings = Settings()
    assert resolve_telegram_chat_id(settings.telegram, TelegramDestination.LINKEDIN) == "111"


def test_target_companies_does_not_fall_back_to_linkedin_channel() -> None:
    telegram = TelegramSettings(
        bot_token="telegram-token",
        chat_id="legacy",
        linkedin_chat_id="111",
        target_companies_chat_id="",
    )
    with pytest.raises(TelegramDestinationConfigError, match="does not fall back"):
        resolve_telegram_chat_id(telegram, TelegramDestination.TARGET_COMPANIES)
    with pytest.raises(TelegramDestinationConfigError, match="TELEGRAM__TARGET_COMPANIES_CHAT_ID"):
        telegram_client_for(telegram, TelegramDestination.TARGET_COMPANIES)


def test_unknown_destination_raises_clear_error() -> None:
    telegram = TelegramSettings(bot_token="telegram-token", chat_id="123")
    with pytest.raises(TelegramDestinationConfigError, match="Unknown Telegram destination"):
        resolve_telegram_chat_id(telegram, "slack")


def test_configured_chat_ids_include_both_destinations_without_mixing() -> None:
    telegram = TelegramSettings(
        bot_token="telegram-token",
        chat_id="legacy",
        linkedin_chat_id="111",
        target_companies_chat_id="222",
    )
    assert configured_telegram_chat_ids(telegram) == frozenset({"111", "222"})


def test_destination_errors_do_not_include_bot_token() -> None:
    telegram = TelegramSettings(bot_token="very-secret-token", chat_id="legacy")
    with pytest.raises(TelegramDestinationConfigError) as target_error:
        resolve_telegram_chat_id(telegram, TelegramDestination.TARGET_COMPANIES)
    with pytest.raises(TelegramDestinationConfigError) as unknown_error:
        resolve_telegram_chat_id(telegram, "unknown-channel")
    assert "very-secret-token" not in str(target_error.value)
    assert "very-secret-token" not in str(unknown_error.value)
