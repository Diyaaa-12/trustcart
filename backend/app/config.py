"""
Application configuration via Pydantic Settings.
All values come from environment variables / .env file.
Secrets are NEVER hardcoded here.
"""
from decimal import Decimal
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- â”€â”€ ---
    DATABASE_URL: str = (
        "postgresql+asyncpg://trustcart:trustcart@localhost:5432/trustcart"
    )

    # --- â”€â”€ ---
    REDIS_URL: str = "redis://localhost:6379/0"

    # --- â”€â”€ ---
    LLM_PROVIDER: str = "gemini"          # "gemini" | "openai"
    GEMINI_MODEL: str = "gemini-2.5-flash"
    GEMINI_API_KEY: str = ""
    OPENAI_API_KEY: str = ""

    # --- â”€â”€ ---
    RAZORPAY_KEY_ID: str = ""
    RAZORPAY_KEY_SECRET: str = ""

    # --- â”€â”€ ---
    MAX_DISCOUNT_BUDGET_PCT: Decimal = Decimal("10.0")
    MAX_PROPOSALS_PER_CART: int = 3
    MAX_ITEM_DISCOUNT_PCT: Decimal = Decimal("20.0")

    # Spend Mandate (AP2 Protocol)
    MANDATE_SECRET: str = "trustcart-ap2-mandate-secret-key-32b"  # noqa: S105
    MANDATE_TTL_MINUTES: int = 30

    # --- â”€â”€ ---
    APP_ENV: str = "development"
    LOG_LEVEL: str = "INFO"

    # --- â”€â”€ ---
    @property
    def razorpay_available(self) -> bool:
        """True only when both Razorpay keys are present."""
        return bool(self.RAZORPAY_KEY_ID and self.RAZORPAY_KEY_SECRET)

    @property
    def mock_checkout(self) -> bool:
        """Use mock checkout when Razorpay keys are absent."""
        return not self.razorpay_available


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings: Settings = get_settings()
