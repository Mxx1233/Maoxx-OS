from uuid import UUID

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def parse_csv_set(value: str) -> frozenset[str]:
    """Parse a comma-separated setting into normalized non-empty values."""

    return frozenset(
        item.strip()
        for item in value.split(",")
        if item.strip()
    )


class Settings(BaseSettings):
    database_url: str
    default_user_id: UUID

    feishu_app_id: str
    feishu_app_secret: str
    feishu_allowed_tenant_keys: str = ""
    feishu_allowed_open_ids: str = ""
    feishu_allowed_chat_types: str = "p2p"
    feishu_supervision_chat_id: str = ""
    feishu_approver_open_ids: str = ""
    feishu_approval_ttl_seconds: int = Field(
        default=1800,
        ge=1,
        le=86400,
    )
    feishu_reply_max_attempts: int = Field(default=3, ge=1, le=5)
    feishu_reply_backoff_seconds: float = Field(default=0.5, ge=0, le=30)

    deployment_github_repository: str = "Mxx1233/Maoxx-OS"
    deployment_github_repository_id: int = Field(default=0, ge=0)
    deployment_github_workflow_id: int = Field(default=0, ge=0)
    deployment_github_workflow_path: str = ".github/workflows/ci.yml"

    app_env: str = "development"
    app_timezone: str = "Europe/Berlin"
    local_storage_root: str = "/app/storage"

    model_config = SettingsConfigDict(
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def allowed_tenant_keys(self) -> frozenset[str]:
        return parse_csv_set(self.feishu_allowed_tenant_keys)

    @property
    def allowed_open_ids(self) -> frozenset[str]:
        return parse_csv_set(self.feishu_allowed_open_ids)

    @property
    def allowed_chat_types(self) -> frozenset[str]:
        return parse_csv_set(self.feishu_allowed_chat_types)

    @property
    def approver_open_ids(self) -> frozenset[str]:
        return parse_csv_set(self.feishu_approver_open_ids)

    @property
    def approval_configuration_valid(self) -> bool:
        return bool(
            self.feishu_supervision_chat_id.strip()
            and self.approver_open_ids
        )


settings = Settings()
