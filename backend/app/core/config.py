from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """App settings, read from environment variables / .env (never hard-code secrets)."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_version: str = "0.1.0"
    mongo_url: str = "mongodb://localhost:27017"
    mongo_db: str = "qapilot"
    storage_dir: str = "storage"  # screenshots; /storage (the qapilot-files volume) in Docker
    jwt_secret: str = "change-me"
    access_token_minutes: int = 15
    refresh_token_days: int = 7
    # Fernet key for test-site credentials (generate with
    # `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`).
    # If empty, a key is derived from JWT_SECRET (fine for development only).
    credentials_key: str = ""
    # LLM (worker/llm). LLM_PROVIDER: gemini | groq | ollama. *_MODEL overrides the default model.
    llm_provider: str = "gemini"
    # Backup providers, tried in order when the current one runs out of tokens (e.g. "gemini" or "gemini,ollama").
    # Providers without an API key are left out.
    llm_fallbacks: str = ""
    llm_timeout_seconds: float = 90.0
    llm_min_interval_seconds: float = 4.0  # pause between LLM calls (Gemini free tier allows ~10-15/minute)
    llm_max_retries: int = 3  # retries on HTTP 429/5xx, with backoff
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.8-flash"
    groq_api_key: str = ""
    groq_model: str = "qwen/qwen3.8-27b"  # needs image input; list yours via GET /openai/v1/models
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.2-vision"
    # Browser (worker/browser)
    browser_channel: str = ""  # "" = bundled Chromium; "msedge"/"chrome" to use an installed browser locally
    browser_headless: bool = True
    action_delay_ms: int = 300  # small pause after every browser action (be gentle with the target site)
    navigation_timeout_ms: int = 20000
    # Hosts the browser must never reach (our own services, cloud metadata). Comma-separated; see core/targets.py.
    blocked_target_hosts: str = "mongo,api,worker,frontend,caddy,metadata.google.internal,metadata"
    # Public deployments (0 / "" = no limit, the default for local use)
    allowed_target_hosts: str = ""  # comma-separated; if set, projects may only test these hosts (and their subdomains)
    registration_code: str = ""  # if set, registering needs this invite code
    max_agent_runs_per_user_per_day: int = 0  # AI runs per user per UTC day (replays don't count: no LLM)
    max_agent_runs_per_day: int = 0  # AI runs for the whole site per UTC day (protects the LLM's free quota)
    max_tests_limit: int = 15  # upper bound for a run's "max tests" option
    # Agents (hard limits, see CLAUDE.md)
    max_steps_per_test: int = 25
    test_timeout_seconds: float = 300.0
    explore_max_pages: int = 12
    explore_max_depth: int = 2
    a11y_max_pages: int = 8  # distinct page templates audited per run (/products/1 and /products/2 count once)
    a11y_max_tabs: int = 80  # Tab presses per page in the keyboard check
    worker_poll_seconds: float = 5.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
