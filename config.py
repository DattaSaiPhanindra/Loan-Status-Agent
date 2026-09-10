from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openrouter_api_key: str = ""
    portal_port: int = 8080
    portal_host: str = "127.0.0.1"
    portal_session_ttl: int = 300
    log_level: str = "INFO"
    artifacts_dir: Path = Path("artifacts")
    evidence_dir: Path = Path("evidence")
    headless: bool = False
    max_steps: int = 25
    step_timeout: int = 10
