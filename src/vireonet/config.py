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
    # auto: an in-memory fake Temporal client in fake adapter mode (unit tests,
    # quick demos), the real server otherwise. real: always the real server, so
    # fake adapters (simulated network, NetBox, ITSM) run full workflows.
    temporal_mode: Literal["auto", "real", "fake"] = "auto"
    adapter_mode: AdapterMode = "fake"
    log_level: str = "INFO"
    api_token: str = ""
    webhook_secret: str = ""
    policy_file: str = "policies/default.yaml"
    execution_file: str = "policies/execution.yaml"
    # Per-intent pre/post check templates. Missing file = built-in defaults.
    check_templates_file: str = "policies/checks.yaml"
    # Per-platform config render templates; override built-ins or add vendors.
    render_templates_file: str = "policies/render_templates.yaml"
    lock_ttl_seconds: int = 1800
    # After post-check passes, hold the change this long before reconciling
    # so an operator can still request a rollback. 0 disables the window.
    post_apply_soak_seconds: int = 0
    wave_size: int = 2
    # After each wave, run that wave's post-check templates before starting
    # the next one; a failure stops the rollout and rolls back.
    wave_health_checks: bool = True
    # Markdown file served on the in-app Help page (default: docs/USER_MANUAL.md).
    user_manual_file: str = ""
    # --- Gitea tab (shown when Gitea answers) ---
    gitea_ui_url: str = ""  # browser address, e.g. https://vireonet.example/gitea
    gitea_api_url: str = ""  # server-side API address, e.g. http://gitea:3000/api/v1
    gitea_embed_url: str = ""  # address used inside VireoNet's page (same origin: /gitea/)
    # Apply adapter settings saved in Adapters -> Configure at start.
    adapter_config_from_db: bool = True
    # gnmic metric-prefix (Prometheus telemetry preset gnmic); empty = none.
    telemetry_metric_prefix: str = ""
    # --- Log platforms (Elasticsearch, OpenSearch, Splunk, Loki, Graylog) ---
    # JSON list: [{"name": "es", "type": "elasticsearch", "url": "https://es:9200",
    #   "token_env": "ES_API_KEY", "index": "syslog-*", "export_index": "vireonet-events"}]
    log_backends: str = "[]"
    # Backends that receive VireoNet events (change finished, flow events, drift).
    log_export_to: list[str] = []
    # --- Vendor controllers ---
    # JSON list: [{"name": "dnac-dc1", "type": "catalyst_center", "url": "https://…",
    #   "username": "…", "password_env": "DNAC_PASSWORD", "verify_tls": true,
    #   "options": {…}}]. Types: catalyst_center, cloudvision, mist, apstra,
    # fortimanager, panorama, nsp, xiq, meraki, aruba_central.
    controllers: str = "[]"
    # Poll interval while a flow waits for a controller job (seconds).
    controller_poll_seconds: int = 15
    # --- Workflow builder (flows) ---
    # Host patterns flows may call with "Call API" (fnmatch; "*" = any).
    flow_http_allowed_hosts: list[str] = ["*"]
    # Always refused: cloud metadata endpoints and link-local addresses.
    flow_http_denied_hosts: list[str] = [
        "169.254.0.0/16",
        "metadata.google.internal",
        "fd00:ec2::254/128",
    ]
    flow_http_verify_tls: bool = True
    flow_scripts_enabled: bool = True
    flow_script_max_seconds: int = 300
    flow_script_memory_mb: int = 512
    # local: a sandboxed subprocess on the worker; sidecar: the separate
    # script-runner service (no secrets, read-only, internal network).
    flow_script_runner: Literal["local", "sidecar"] = "local"
    flow_script_runner_url: str = "http://script-runner:8100"
    flow_script_runner_secret: str = ""
    # Refuse to run scripts unless they run as non-root with no network.
    flow_script_require_isolation: bool = False
    # Modules flow scripts may import (empty = the safe default list).
    flow_script_allowed_imports: list[str] = []
    # Refuse "Call API" to private / loopback addresses (RFC 1918, 127/8, ::1).
    flow_http_block_private: bool = False
    # Token for the adapter event webhooks /webhooks/flows/source/<source>
    # (header X-VireoNet-Event-Token or Bearer). Empty: VIREONET_WEBHOOK_SECRET.
    flow_event_token: str = ""
    # Optional Kafka trigger source; empty = off.
    kafka_bootstrap: str = ""
    kafka_group_id: str = "vireonet-flows"
    kafka_auto_offset_reset: str = "latest"
    kafka_security_protocol: str = "PLAINTEXT"
    kafka_sasl_mechanism: str = ""
    kafka_username: str = ""
    kafka_password: str = ""
    abort_threshold: int = 1

    servicenow_base_url: str = ""
    servicenow_auth_mode: Literal["oauth", "basic"] = "oauth"
    servicenow_client_id: str = ""
    servicenow_client_secret: str = ""
    servicenow_username: str = ""
    servicenow_password: str = ""

    # ITSM provider for ticket attachments (Excel config push), comments and
    # closure: auto | none | fake | servicenow | jira | freshservice | bmc_helix.
    # "auto" = fake in fake mode, ServiceNow in real mode when configured.
    itsm_provider: Literal[
        "auto", "none", "fake", "servicenow", "jira", "freshservice", "bmc_helix"
    ] = "auto"
    itsm_verify_tls: bool = True
    jira_base_url: str = ""
    jira_email: str = ""  # Jira Cloud: account email + API token
    jira_api_token: str = ""
    jira_pat: str = ""  # Jira Data Center / Server: personal access token
    jira_close_transition: str = "Done"
    freshservice_base_url: str = ""
    freshservice_api_key: str = ""
    bmc_base_url: str = ""
    bmc_username: str = ""
    bmc_password: str = ""
    # Your own TextFSM templates: a directory with an ntc-style "index" file.
    # They are tried before the bundled ntc-templates.
    textfsm_user_dir: str = "policies/textfsm"
    # End-to-end automation from the ITSM tool: a ticket entering one of these
    # states (name or raw value, case-insensitive) triggers the Excel push.
    # ServiceNow Change Request "Implement" is -1.
    itsm_trigger_states: list[str] = ["implement", "-1"]
    # When the ticket state triggers the push, that state is the approval (CAB
    # in the ITSM tool): no approval click in VireoNet. False: still wait for
    # an approver in VireoNet (manual approval of an automatic intake).
    itsm_trigger_approves: bool = True
    # Fully automatic pushes run only inside the ticket's planned window; with
    # this on (default), a trigger without a planned start/end is refused.
    itsm_require_planned_window: bool = True
    jira_planned_start_field: str = ""  # e.g. customfield_10042
    jira_planned_end_field: str = ""
    # Site for changes created from a ticket that names none.
    itsm_default_site: str = "dc1"
    # Excel config push limits.
    excel_max_bytes: int = 2_000_000
    excel_max_device_config_bytes: int = 200_000

    netbox_base_url: str = ""
    netbox_token: str = ""

    awx_base_url: str = ""
    awx_token: str = ""
    # AWX REST prefix: /api/v2 (AWX / AAP <= 2.4) or /api/controller/v2 (AAP 2.5+ gateway).
    awx_api_prefix: str = "/api/v2"
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
    # Machine registration of ZTP devices (POST /ztp/devices) without a user
    # login: send X-VireoNet-ZTP-Token with this value. Empty = operator login only.
    ztp_registration_token: str = ""
    # /metrics: when set, Prometheus must send "Authorization: Bearer <token>".
    metrics_token: str = ""
    # Login lockout: this many failures for a username within the window.
    login_max_failures: int = 5
    login_lockout_seconds: int = 300

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

    # Interface telemetry (traffic in/out) for topology and ML. none | fake |
    # zabbix | prometheus | librenms | prtg | solarwinds. Fake adapter mode
    # with "none" uses synthetic traffic. gNMI/gRPC streaming: run gnmic with
    # its Prometheus output and use prometheus + preset "gnmic"; SNMP via
    # snmp_exporter uses preset "snmp_exporter".
    telemetry_backend: Literal[
        "none", "fake", "zabbix", "prometheus", "librenms", "prtg", "solarwinds"
    ] = "none"
    telemetry_base_url: str = ""
    telemetry_token: str = ""
    telemetry_username: str = ""
    telemetry_password: str = ""
    telemetry_verify_tls: bool = True
    telemetry_prometheus_preset: Literal["snmp_exporter", "gnmic"] = "snmp_exporter"

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
    git_default_branch: str = "main"  # main or master: holds the approved running config
    # Where device configs live in Git. per_device: repo netops/config-<device>,
    # file backups/running-config.txt. monorepo: one repo (git_config_repo, or
    # git_repo when empty) with one file per device at git_config_path.
    git_layout: Literal["per_device", "monorepo"] = "per_device"
    git_config_repo: str = ""
    git_config_path: str = "{device}/config"
    # Change rollback source. git: restore each device's config from the
    # main/master commit recorded at pre-check (the config before this change),
    # falling back to the AWX pre-change backup only if Git cannot serve it.
    # backup: always restore the AWX pre-change backup.
    rollback_source: Literal["git", "backup"] = "git"

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


def flow_security_warnings(settings: Settings) -> list[str]:
    """Flow settings that are allowed but weaker than recommended."""
    out: list[str] = []
    if (
        settings.flow_scripts_enabled
        and settings.flow_script_runner == "local"
        and not settings.flow_script_require_isolation
    ):
        out.append(
            "flow scripts run on the worker without required isolation: set "
            "VIREONET_FLOW_SCRIPT_RUNNER=sidecar (recommended) or VIREONET_FLOW_SCRIPT_REQUIRE_ISOLATION=true"
        )
    if settings.flow_script_runner == "sidecar" and settings.flow_script_runner_secret in (
        "",
        "change-me-runner-secret",
    ):
        out.append("VIREONET_FLOW_SCRIPT_RUNNER_SECRET is empty or the example value")
    if "*" in settings.flow_http_allowed_hosts and not settings.flow_http_block_private:
        out.append(
            "Call API steps may reach any host, including private addresses (VIREONET_FLOW_HTTP_ALLOWED_HOSTS)"
        )
    return out


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
