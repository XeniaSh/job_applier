from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path


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
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    resumes_dir: Path = Path("resumes")
    candidate_preferred_language: str = "en"
    candidate_grammatical_gender: str = "neutral"
    pipeline_interval_seconds: int = 300
    telegram_poll_interval_seconds: int = 2

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )
