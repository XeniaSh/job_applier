from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path
from pydantic import field_validator
from pydantic_settings import NoDecode
from typing import Annotated


class Settings(BaseSettings):
    llm_api_url: str
    llm_api_key: str
    llm_model: str
    hh_user_agent: str = "job-vacancy-analyzer/0.1 contact@example.com"
    linkedin_email_imap_host: str = "imap.gmail.com"
    linkedin_email_imap_port: int = 993
    linkedin_email_username: str = ""
    linkedin_email_password: str = ""
    linkedin_email_folder: str = "INBOX"
    linkedin_email_search_days: int = 7
    linkedin_email_mark_as_read: bool = False
    linkedin_email_incremental_enabled: bool = True
    linkedin_email_bootstrap_message_limit: int = 500
    linkedin_email_bootstrap_lookback_days: int = 7
    linkedin_email_batch_size: int = 200
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    resumes_dir: Path = Path("resumes")
    candidate_preferred_language: str = "en"
    candidate_grammatical_gender: str = "neutral"
    pipeline_interval_seconds: int = 300
    telegram_poll_interval_seconds: int = 2
    preparing_recovery_timeout_seconds: int = 600
    undo_window_seconds: int = 600
    greenhouse_boards: Annotated[list[str], NoDecode] = []

    @field_validator("greenhouse_boards", mode="before")
    @classmethod
    def parse_greenhouse_boards(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            chunks = value.replace("\r", "\n").replace(",", "\n").split("\n")
            return [item.strip() for item in chunks if item.strip()]
        if isinstance(value, list):
            cleaned: list[str] = []
            for item in value:
                if isinstance(item, str) and item.strip():
                    cleaned.append(item.strip())
            return cleaned
        return []

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )
