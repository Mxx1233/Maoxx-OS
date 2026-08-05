from uuid import UUID

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str
    default_user_id: UUID

    feishu_app_id: str
    feishu_app_secret: str

    app_env: str = "development"
    app_timezone: str = "Europe/Berlin"
    local_storage_root: str = "/app/storage"

    model_config = SettingsConfigDict(
        case_sensitive=False,
        extra="ignore",
    )


settings = Settings()
