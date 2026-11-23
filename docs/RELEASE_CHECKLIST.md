# VireoNet release checklist

## Engineering gates

- [ ] Backend Ruff and strict mypy pass.
- [ ] Backend unit and integration tests pass.
- [ ] Frontend ESLint, Vitest and production build pass.
- [ ] Docker image builds reproducibly and is tagged by immutable digest.
- [ ] Alembic upgrade is tested against a production-like PostgreSQL instance.
- [ ] Backup/restore drill passes, including AWX artifacts and Git config repos.
- [ ] Ansible playbook syntax/lint and runner contract tests pass.
- [ ] Adapter contract tests pass for each supported NetBox, AWX, ServiceNow,
      Git provider, DHCP, Batfish and lab backend version.
- [ ] Containerlab/EVE-NG canary demonstrates apply, post-check, rollback and
      reconciliation for each supported change type.
- [ ] Security dependency scan, secret scan, container scan and penetration
      test findings are triaged.

## Operational gates

- [ ] Real-mode startup refuses missing/default secrets.
- [ ] Monitoring covers API/worker health, Temporal queue backlog, stuck
      workflows, AWX jobs, lock expiry, rollback failure, drift and pending
      reconciliation/closure.
- [ ] Runbooks exist for change failure, rollback, ZTP, restore, upgrades and
      credential rotation.
- [ ] SLOs, RPO/RTO, retention and support escalation are agreed.
- [ ] A production canary and rollback plan are approved.

## Enterprise/product gates

- [ ] Tenant isolation and tenant-scoped authorization are implemented before
      any multi-customer SaaS deployment.
- [ ] OIDC and MFA policy are implemented and tested before enterprise SSO
      claims are made.
- [ ] Customer-specific secret and data boundaries are documented.
- [ ] Supported platform/vendor/version matrix is published.
- [ ] API versioning and deprecation policy is published.
- [ ] Licensing, packaging, support and upgrade policy are approved.

## Release decision

A release is not production-ready while any P0/P1 security or execution-safety
gate is open. A passing fake-adapter test suite alone is not evidence of
production readiness.
