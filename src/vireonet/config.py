from __future__ import annotations

from typing import Literal
from urllib.parse import urlparse

from pydantic_settings import BaseSettings, SettingsConfigDict

AdapterMode = Literal["fake", "real", "recorded"]

# Well-known defaults that must never be used outside dev/fake mode.
DEFAULT_JWT_SECRET = "dev-only-change-me"
DEFAULT_DB_PASSWORD = "vireonet"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VIREONET_", env_file=".env", extra="ignore")

    # Explicit dev flag. Never inferred from adapter_mode or anything else:
    # dev mode relaxes webhook auth (see verify_webhook), fake mode does not.
    dev: bool = False

    database_url: str = "postgresql+asyncpg://vireonet:vireonet@localhost:5432/vireonet"  # plain str: tests use sqlite+aiosqlite in-memory URLs
    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "default"
    temporal_task_queue: str = "vireonet-changes"
    adapter_mode: AdapterMode = "fake"
    log_level: str = "INFO"
    api_token: str = ""
    webhook_secret: str = ""
    policy_file: str = "policies/default.yaml"
    execution_file: str = "policies/execution.yaml"
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

    # Lab backends (E4): eveng | containerlab | both. Containerlab drives
    # the containerlab CLI on the lab host over SSH.
    lab_backend: str = "eveng"
    containerlab_host: str = ""
    containerlab_user: str = ""
    containerlab_key_path: str = ""
    containerlab_password: str = ""

    # Custom scripts (E3/E5): the manifest lists which scripts may run, at
    # which SHA. script_repo is the default repo the manifest entries point
    # at; awx_execution_environment is the AWX Execution Environment the
    # job templates run in (device sessions run only inside it).
    script_repo: str = "netops/scripts"
    script_manifest: str = "policies/scripts.yaml"
    awx_execution_environment: str = "VireoNet EE"

    dhcp_server: Literal["isc", "kea"] = "isc"
    dhcp_config_path: str = "dhcp/dhcpd.conf"
    dhcp_tftp_server: str = "10.0.0.5"
    dhcp_http_base_url: str = "http://10.0.0.5/ztp"
    dhcp_pnp_server_url: str = "https://pnp.example.com"
    dhcp_git_repo: str = ""
    dhcp_reload_template: str = "dhcp-reload"
    dhcp_verify_retries: int = 12
    dhcp_verify_interval_seconds: float = 5.0
    ztp_token_ttl_seconds: int = 900
    # Site-specific provisioning defaults. For multi-site deployments, define
    # these in a per-site provisioning profile rather than sharing one pool.
    ztp_ip_pool: str = "10.0.1.0/24"
    ztp_management_interface: str = "GigabitEthernet0/0"

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
    jwt_secret: str = (
        DEFAULT_JWT_SECRET  # MUST be set from env/Vault in prod; startup warns if default is used
    )
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 480
    admin_user: str | None = None
    admin_password: str | None = None

    # CORS origins for the browser UI, comma-separated.
    # The UI runs on a different origin (5173) than the API (8000), so
    # without this the browser blocks every fetch and login always fails
    # in the UI even though curl works. Add your LAN origin here for
    # remote access, e.g. http://10.228.197.24:5173
    cors_origins: str = (
        "http://localhost:5173,http://localhost:4173,http://localhost:8000,http://127.0.0.1:5173"
    )

    # Drift scanning (D3): one scheduled scan per site, nightly by default.
    drift_sites: list[str] = ["dc1"]
    drift_cron: str = "0 2 * * *"
    # Shared secret for POST /drift/events (device/syslog event triggers).
    # Empty is fail-closed: the endpoint returns 401 until this is set.
    drift_event_secret: str = ""

    # Drift resolution (D4): exception/emergency/incident policy.
    drift_exception_max_days: int = 30
    drift_emergency_deadline_hours: int = 24
    drift_incident_severity_threshold: str = "high"

    # Drift hardening (D6): scan concurrency and policy file locations.
    drift_max_concurrent: int = 5
    drift_ignore_path: str = "policies/drift_ignore.yaml"
    drift_severity_policy: str = "policies/drift.yaml"
    # Compliance-rule file (policies/*.yaml); empty means no compliance
    # rules are configured, so the D6 NetBox-field check is skipped.
    drift_compliance_rules: str = ""


def get_settings() -> Settings:
    return Settings()


def validate_drift_settings(settings: Settings) -> list[str]:
    """D6 startup checks for drift settings. Pure: returns problems, never raises.

    Callers decide the consequence: real mode refuses to start, fake /
    recorded mode only warns (see main.create_app). Checks:

    - scan concurrency 1-50 (drift_max_concurrent);
    - drift_cron is a valid 5-field cron (same shape as
      drift.schedules._valid_cron; croniter is NOT a dependency, so this is
      the same simple field check, not a croniter parse);
    - exception/emergency/incident policy values are in range / known;
    - the ignore-rule file exists;
    - the severity policy's section names are known sections
      (drift.sections.SECTION_NAMES);
    - when a compliance-rule file is configured, it parses and every
      referenced config-context key is a string (best-effort: this does
      NOT invent a NetBox schema check — NetBox has no fixed device-model
      schema in this codebase, so only the rule file itself is validated).
    """
    from pathlib import Path

    import yaml

    from vireonet.drift.sections import SECTION_NAMES

    problems: list[str] = []
    if not 1 <= settings.drift_max_concurrent <= 50:
        problems.append(f"drift_max_concurrent must be 1-50, got {settings.drift_max_concurrent}")
    try:
        from vireonet.drift.schedules import _valid_cron
    except ImportError:  # pragma: no cover - schedules is always importable here
        _valid_cron = None  # type: ignore[assignment]
    if _valid_cron is not None and not _valid_cron(settings.drift_cron):
        problems.append(f"drift_cron is not a valid 5-field cron: {settings.drift_cron!r}")
    if not 1 <= settings.drift_exception_max_days <= 365:
        problems.append(
            f"drift_exception_max_days must be 1-365, got {settings.drift_exception_max_days}"
        )
    if not 1 <= settings.drift_emergency_deadline_hours <= 168:
        problems.append(
            "drift_emergency_deadline_hours must be 1-168, "
            f"got {settings.drift_emergency_deadline_hours}"
        )
    if settings.drift_incident_severity_threshold not in {"critical", "high", "medium", "low"}:
        problems.append(
            "drift_incident_severity_threshold must be one of "
            f"critical|high|medium|low, got {settings.drift_incident_severity_threshold!r}"
        )
    if not Path(settings.drift_ignore_path).exists():
        problems.append(f"drift ignore-rule file not found: {settings.drift_ignore_path}")
    policy_path = Path(settings.drift_severity_policy)
    if policy_path.exists():
        try:
            policy = yaml.safe_load(policy_path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            problems.append(f"drift severity policy does not parse: {exc}")
            policy = {}
        if isinstance(policy, dict):
            known = set(SECTION_NAMES)
            for key in ("critical_sections", "high_sections", "managed_sections"):
                for section in policy.get(key) or []:
                    if str(section) not in known:
                        problems.append(
                            f"drift severity policy {key} references unknown "
                            f"section {str(section)!r} (known: {sorted(known)})"
                        )
    else:
        problems.append(f"drift severity policy not found: {settings.drift_severity_policy}")
    rules_path = settings.drift_compliance_rules
    if rules_path:
        file = Path(rules_path)
        if not file.exists():
            problems.append(f"drift compliance-rule file not found: {rules_path}")
        else:
            try:
                raw = yaml.safe_load(file.read_text(encoding="utf-8"))
            except (OSError, yaml.YAMLError) as exc:
                problems.append(f"drift compliance rules do not parse: {exc}")
                raw = None
            if raw is not None:
                rules = raw.get("rules", raw) if isinstance(raw, dict) else raw
                if not isinstance(rules, list):
                    problems.append(
                        "drift compliance rules must be a list (or a mapping with a 'rules' list)"
                    )
                else:
                    for rule in rules:
                        if not isinstance(rule, dict):
                            problems.append(f"drift compliance rule is not a mapping: {rule!r}")
                            continue
                        for key, value in (rule.get("context") or {}).items():
                            if not isinstance(key, str) or not isinstance(value, str):
                                problems.append(
                                    f"drift compliance rule {rule.get('name', '?')!r}: "
                                    "config-context keys and values must be strings"
                                )
    return problems


def validate_settings(settings: Settings) -> list[str]:
    """Fail-closed startup checks. Only enforced in real mode.

    Fake/recorded mode always passes so unit tests and local demos can
    start with defaults; real mode refuses to start with default secrets
    or with no webhook authentication configured (unless dev=True, which
    is an explicit, separate opt-in).
    """
    if settings.adapter_mode != "real":
        return []
    problems: list[str] = []
    if settings.jwt_secret == DEFAULT_JWT_SECRET:
        problems.append("jwt_secret is the dev default; set VIREONET_JWT_SECRET")
    try:
        db_password = urlparse(settings.database_url).password
    except ValueError:
        db_password = None
        problems.append("database_url is not a valid URL")
    if db_password is None:
        problems.append("database_url has no password; set a real database password")
    elif db_password == DEFAULT_DB_PASSWORD:
        problems.append("database_url uses the default password; set a real database password")
    if not settings.dev:
        if not settings.webhook_secret and not settings.api_token:
            problems.append(
                "no webhook_secret or api_token configured; set one or VIREONET_DEV=true"
            )
        if not settings.gitlab_webhook_token:
            problems.append("no gitlab_webhook_token configured; set one or VIREONET_DEV=true")
    return problems
