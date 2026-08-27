from pathlib import Path

from pydantic import AnyHttpUrl, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="PAPERTRAIL_")

    processor_config_path: Path = Path("config/processors.yaml")
    work_dir: Path = Path(".papertrail-data")
    mineru_base_url: AnyHttpUrl = AnyHttpUrl("http://127.0.0.1:8000")
    ollama_base_url: AnyHttpUrl = AnyHttpUrl("http://127.0.0.1:11434")
    ollama_model: str = "llama3.1:8b"
    fetch_connect_timeout_seconds: float = Field(default=3, gt=0, le=30)
    fetch_read_timeout_seconds: float = Field(default=15, gt=0, le=120)
    fetch_max_bytes: int = Field(default=10_000_000, gt=0, le=50_000_000)
    retry_limit: int = Field(default=3, ge=1, le=3)
