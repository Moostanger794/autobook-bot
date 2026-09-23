from datetime import time
from functools import lru_cache
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    bot_token: str = ""
    admin_ids: str = ""
    database_url: str = "postgresql+asyncpg://localhost/autobook"
    api_key: str = ""
    business_name: str = "AutoBook Bot"
    business_address: str = ""
    business_phone: str = ""
    business_telegram: str = ""
    timezone: str = "Europe/Moscow"
    work_start: time = time(10)
    work_end: time = time(20)
    work_days: str = "0,1,2,3,4,5,6"
    slot_step_minutes: int = Field(default=30, ge=5, le=120)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        ZoneInfo(value)
        return value

    @property
    def admins(self) -> frozenset[int]:
        ids = frozenset(int(part.strip()) for part in self.admin_ids.split(",") if part.strip())
        if any(user_id <= 0 for user_id in ids):
            raise ValueError("ADMIN_IDS must contain positive user IDs")
        return ids

    @property
    def weekdays(self) -> frozenset[int]:
        days = frozenset(int(part.strip()) for part in self.work_days.split(",") if part.strip())
        if not days or any(day not in range(7) for day in days):
            raise ValueError("WORK_DAYS must contain weekdays 0..6")
        return days

    @model_validator(mode="after")
    def valid_schedule(self) -> "Settings":
        if self.work_start >= self.work_end:
            raise ValueError("WORK_START must precede WORK_END")
        _ = self.weekdays
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


def validate_runtime(settings: Settings, *, bot: bool = False, api: bool = False) -> None:
    if bot and not settings.bot_token:
        raise ValueError("BOT_TOKEN is required")
    if api and not settings.api_key:
        raise ValueError("API_KEY is required")
    if not settings.database_url.startswith("postgresql+asyncpg://"):
        raise ValueError("DATABASE_URL must use postgresql+asyncpg")
    if bot and not settings.admins:
        raise ValueError("ADMIN_IDS must contain at least one Telegram ID")
