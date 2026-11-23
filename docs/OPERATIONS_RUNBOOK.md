# VireoNet operations runbook

## Scope

This runbook describes the single-instance production Compose profile. It is
not an HA design. Keep the API, worker, PostgreSQL, Temporal, AWX, NetBox,
ServiceNow, Vault, Git provider, Batfish and network management plane on
restricted management networks.

## Before first deployment

1. Review `docker-compose.production.yml` and supply secrets from a secret
   manager. Never commit `.env` or real credentials.
2. Set `VIREONET_ADAPTER_MODE=real` (the production file does this) and
   `VIREONET_DEV=false`.
3. Set a unique JWT signing secret, webhook secrets, database password and
   adapter credentials. Rotate any values previously used in development.
4. Confirm NetBox inventory serials, device platforms, site data and management
   reachability before enabling ZTP.
5. Confirm AWX templates and execution environments match
   `policies/execution.yaml`; run the AWX template validation before change
   execution.
6. Validate DHCP scope, TFTP/HTTP bootstrap reachability, reserved addresses,
   and the ZTP management interface for the target site.
7. Restrict published ports to a reverse proxy or management subnet. Terminate
   TLS at a trusted reverse proxy and configure exact CORS origins.
8. Take a PostgreSQL backup and verify that it can be restored to an isolated
   test instance before onboarding production devices.

## Start and observe

```bash
docker compose -f docker-compose.production.yml config
docker compose -f docker-compose.production.yml up -d --build
docker compose -f docker-compose.production.yml ps
docker compose -f docker-compose.production.yml logs -f api worker temporal
```

Check `/healthz`, `/adapters/health`, `/system/health`, Temporal task-queue
health and AWX job status. A healthy HTTP endpoint does not prove that network
execution integrations are healthy.

## Change execution failure

- Do not release device locks manually while an AWX job may still be running.
- Inspect the Temporal workflow history and AWX job state for the change ticket.
- If the AWX launch outcome is unknown, locate the job using the ticket and
  correlation evidence before retrying. Do not submit a second apply by guess.
- If rollback is confirmed, inspect post-rollback state collection and verify
  the device against the pre-change configuration hash.
- If rollback is failed or unknown, keep the lock, stop further changes to the
  device, escalate to the network operator and preserve AWX logs/backups.
- A change in `closure_pending` means device checks/reconciliation completed
  but external NetBox/ServiceNow closure was not confirmed. An approver may
  retry using `POST /changes/{id}/retry-close` after confirming the external
  systems are reachable.

## ZTP failure

- Do not re-register a serial to mint another token. Use the authenticated
  retry action and confirm the prior workflow is not still provisioning.
- Confirm NetBox device identity and serial before retry.
- Check DHCP reservation deployment and validation, bootstrap URL reachability,
  callback token expiry/consumption, device identity result, AWX day-1 job and
  NetBox activation.
- A device is not active until the activation activity returns success.
- Remove stale reservations only after confirming the device is not relying on
  them to complete provisioning.

## Backup and restore

- PostgreSQL contains change/audit state, persisted backup job references,
  configuration versions and ZTP token hashes. Protect it as production
  configuration data.
- Back up PostgreSQL using your organization's approved encrypted backup
  process; retain encryption keys separately.
- Back up AWX job artifacts and execution-environment content according to
  AWX's supported backup procedure. A database record containing an AWX job
  ID is not a substitute for the actual AWX backup artifact.
- Back up Git configuration repositories and the Vault secret store using
  their native supported procedures.
- Perform a quarterly isolated restore drill. Verify migrations, audit history,
  config version retrieval, Temporal workflow recovery and AWX job artifact
  availability before declaring the restore successful.

## Upgrade

1. Read release notes and migration changes.
2. Take verified backups of PostgreSQL, Git config repos, AWX artifacts and
   Vault data.
3. Deploy to staging and run backend, frontend, migration and adapter contract
   suites.
4. Apply migrations once, using the API startup command; the worker must not
   race schema migrations.
5. Deploy API and worker from the same immutable image tag.
6. Run smoke tests using fake/recorded adapters first; run a controlled
   canary against a lab device before production changes.
7. Keep the previous image available for application rollback. Database
   downgrade is not assumed safe unless the specific migration documents it.

## Incident evidence

Preserve: change ID/ticket, Temporal workflow ID/history, AWX job IDs and
artifacts, pre-change backup references and hashes, post-change collection,
diffs, audit transitions, NetBox/ServiceNow responses, relevant adapter logs,
and timestamps. Redact credentials, bearer tokens and provisioning tokens.
