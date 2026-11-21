import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'data' / 'grader.db'}")

    llm_provider: str = os.getenv("LLM_PROVIDER", "mock").strip().lower()
    llm_temperature: float = float(os.getenv("LLM_TEMPERATURE", "0"))
    llm_timeout: float = float(os.getenv("LLM_TIMEOUT", "180"))
    llm_json_mode: bool = _bool("LLM_JSON_MODE", True)
    llm_max_retries: int = int(os.getenv("LLM_MAX_RETRIES", "5"))
    llm_max_wait: float = float(os.getenv("LLM_MAX_WAIT", "60"))

    groq_api_key: str = os.getenv("GROQ_API_KEY", "").strip()
    groq_base_url: str = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
    groq_model: str = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

    ollama_url: str = os.getenv("OLLAMA_URL", "http://localhost:11434")
    ollama_model: str = os.getenv("OLLAMA_MODEL", "gemma3:12b")

    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "").strip()
    anthropic_model: str = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5")

    openai_api_key: str = os.getenv("OPENAI_API_KEY", "").strip()
    openai_base_url: str = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    weak_threshold: float = float(os.getenv("WEAK_THRESHOLD", "0.7"))
    teacher_examples: int = int(os.getenv("TEACHER_EXAMPLES", "3"))
    check_temperature: float = float(os.getenv("CHECK_TEMPERATURE", "0.7"))

    seed_demo: bool = _bool("SEED_DEMO", True)
    mock_delay: float = float(os.getenv("MOCK_DELAY", "0"))


settings = Settings()
