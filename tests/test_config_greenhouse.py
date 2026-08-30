import pytest
from pydantic import ValidationError

from app.config import Settings


def test_greenhouse_boards_parse_multiline_and_csv(monkeypatch) -> None:
    monkeypatch.setenv("LLM_API_URL", "https://llm.local")
    monkeypatch.setenv("LLM_API_KEY", "secret")
    monkeypatch.setenv("LLM_MODEL", "model")
    monkeypatch.setenv("GREENHOUSE_BOARDS", "stripe, notion\ncanva")

    settings = Settings()
    assert settings.greenhouse_boards == ["stripe", "notion", "canva"]


def test_telegram_job_feed_min_match_defaults_to_strong_and_accepts_aliases(monkeypatch) -> None:
    monkeypatch.setenv("LLM_API_URL", "https://llm.local")
    monkeypatch.setenv("LLM_API_KEY", "secret")
    monkeypatch.setenv("LLM_MODEL", "model")
    monkeypatch.delenv("TELEGRAM__JOB_FEED__MIN_MATCH", raising=False)

    settings = Settings()
    assert settings.telegram.job_feed.min_match == "STRONG"
    assert settings.telegram.bot_token == ""
    assert settings.telegram.chat_id == ""

    monkeypatch.setenv("TELEGRAM__BOT_TOKEN", "telegram-token")
    monkeypatch.setenv("TELEGRAM__CHAT_ID", "123")
    monkeypatch.setenv("TELEGRAM__JOB_FEED__MIN_MATCH", "potential_match")
    loaded = Settings()
    assert loaded.telegram.bot_token == "telegram-token"
    assert loaded.telegram.chat_id == "123"
    assert loaded.telegram.job_feed.min_match == "POTENTIAL"

    monkeypatch.setenv("TELEGRAM__JOB_FEED__MIN_MATCH", "WEAK")
    with pytest.raises(ValidationError):
        Settings()
