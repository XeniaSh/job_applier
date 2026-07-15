from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    llm_api_url: str
    llm_api_key: str
    llm_model: str
    hh_user_agent: str = "job-vacancy-analyzer/0.1 contact@example.com"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )
