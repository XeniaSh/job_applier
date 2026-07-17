from app.config import Settings


def test_greenhouse_boards_parse_multiline_and_csv(monkeypatch) -> None:
    monkeypatch.setenv("LLM_API_URL", "https://llm.local")
    monkeypatch.setenv("LLM_API_KEY", "secret")
    monkeypatch.setenv("LLM_MODEL", "model")
    monkeypatch.setenv("GREENHOUSE_BOARDS", "stripe, notion\ncanva")

    settings = Settings()
    assert settings.greenhouse_boards == ["stripe", "notion", "canva"]
