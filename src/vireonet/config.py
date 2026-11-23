from __future__ import annotations

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

AdapterMode = Literal["fake", "real", "recorded"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VIREONET_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://vireonet:vireonet@localhost:5432/vireonet"  # plain str: tests use sqlite+aiosqlite in-memory URLs
    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "default"
    temporal_task_queue: str = "vireonet-changes"
    adapter_mode: AdapterMode = "fake"
    log_level: str = "INFO"
    api_token: str = ""
    webhook_secret: str = ""
    policy_file: str = "policies/default.yaml"
    lock_ttl_seconds: int = 1800
    wave_size: int = 2
    abort_threshold: int = 1

    servicenow_base_url: str = ""
    servicenow_auth_mode: Literal["oauth", "basic"] = "oauth"
    servicenow_client_id: str = ""
    servicenow_client_secret: str = ""
    servicenow_username: str = ""
    servicenow_password: str = ""

    netbox_base_url: str = ""
    netbox_token: str = ""

    awx_base_url: str = ""
    awx_token: str = ""
    gitlab_base_url: str = ""
    gitlab_token: str = ""
    gitlab_project_id: str = ""
    gitlab_webhook_token: str = ""

    batfish_host: str = "localhost"
    batfish_port: int = 9996
    batfish_snapshot_dir: str = "/tmp/vireonet/batfish"

    vault_addr: str = ""
    vault_role_id: str = ""
    vault_secret_id: str = ""
    vault_kv_mount: str = "secret"
    vault_bootstrap_policy: str = "bootstrap"

    eveng_base_url: str = ""
    eveng_username: str = ""
    eveng_password: str = ""

    dhcp_server: Literal["isc", "kea"] = "isc"
    dhcp_config_path: str = "dhcp/dhcpd.conf"
    dhcp_tftp_server: str = "10.0.0.5"
    dhcp_http_base_url: str = "http://10.0.0.5/ztp"
    dhcp_pnp_server_url: str = "https://pnp.example.com"
    ztp_token_ttl_seconds: int = 900

    # Notifications (M7)
    notification_email_to: str = ""
    notification_email_from: str = "vireonet@example.com"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    teams_webhook_url: str = ""
    slack_webhook_url: str = ""
    notification_default_channel: Literal["email", "teams", "slack"] = "slack"

    # Monitoring (M7) — none | alertmanager | librenms
    monitoring_backend: Literal["none", "alertmanager", "librenms"] = "none"
    monitoring_base_url: str = ""
    monitoring_token: str = ""
    monitoring_silence_minutes: int = 60

    notification_webhook_url: str = ""
    adapter_timeout_seconds: float = 10.0
    adapter_max_retries: int = 3
    adapter_failure_threshold: int = 5
    adapter_reset_timeout_seconds: float = 30.0

    # Git provider (M10): gitlab | github | bitbucket | gitea
    git_provider: str = "gitlab"
    git_base_url: str = ""
    git_repo: str = ""
    git_token_secret: str = "git/token"  # Vault KV path, never the token itself
    git_default_branch: str = "main"

    # IAM / JWT (M9)
    jwt_secret: str = "dev-only-change-me"  # MUST be set from env/Vault in prod; startup warns if default is used
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 480
    admin_user: str | None = None
    admin_password: str | None = None

    # CORS origins for the browser UI, comma-separated.
    # The UI runs on a different origin (5173) than the API (8000), so
    # without this the browser blocks every fetch and login always fails
    # in the UI even though curl works. Add your LAN origin here for
    # remote access, e.g. http://10.228.197.24:5173
    cors_origins: str = "http://localhost:5173,http://localhost:4173,http://localhost:8000,http://127.0.0.1:5173"


def get_settings() -> Settings:
    return Settings()
