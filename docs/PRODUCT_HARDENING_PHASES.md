# VireoNet product hardening — phases 1–4

This is the delivery ledger for the commercial-product hardening programme.
Changes are isolated on `product-hardening/phases-1-4`; `main` is unchanged.

## Delivery rules

- Preserve FastAPI + Temporal + PostgreSQL + NetBox + AWX.
- Unknown network outcomes are failures or pending states, never success.
- Every phase requires executable tests and CI evidence before it is called
  complete.
- Fake-adapter tests alone do not establish production readiness.
- Keep secrets out of workflow payloads, logs, frontend storage and AWX
  `extra_vars`.

## Phase 1 — execution safety and recovery (P0)

**Implemented slices**
- [x] ZTP registration requires operator role; serial lookup requires viewer.
- [x] ZTP callback tokens are single-use, expiring and verified under a
      database row lock.
- [x] NetBox inventory lookup and serial consistency are required before ZTP
      enrollment; duplicate registration cannot silently mint a new token.
- [x] Rollback terminal state requires explicit `ok=true`; negative or
      uncertain rollback keeps the device lock and backup references.
- [x] AWX backup job IDs are persisted in the PostgreSQL change intent before
      apply; worker memory is only a compatibility cache.
- [x] Owner-checked device lock lease renewal is available and checked before
      apply, rollback and post-check.
- [x] External closure failure is represented as `closure_pending`; an
      approver-only retry endpoint performs external closure before transition
      to `closed`.
- [x] ZTP only reports `active` after activation confirms success.

**Still open**
- [ ] AWX launch idempotency/reconciliation after a timeout. AWX's launch API
      is non-idempotent; a deterministic correlation key alone is not enough.
      Inspect the AWX job state before retrying an uncertain launch.
- [ ] Continuous Temporal heartbeat/fencing for long-running execution.
      Current lease renewal is at workflow boundaries, not a distributed
      fencing protocol.
- [ ] Persisted AWX artifact URI/checksum/retention metadata in addition to
      the durable job ID.
- [ ] Worker restart/replay, duplicate-delivery, rollback-timeout, ZTP
      authorization and activation integration tests.
- [ ] Run the test suite and obtain green CI evidence.

## Phase 2 — change assurance and reconciliation (P1)

**Implemented slices**
- [x] Failed or empty post-change state collection is rejected; it cannot be
      committed as a new baseline.
- [x] Built-in VLAN-add and interface-set reconciliation have explicit
      NetBox write scopes; unknown change types remain fail-closed.
- [x] GitHub webhook uses native `X-Hub-Signature-256` HMAC verification.
- [x] Focused tests added for GitHub signature verification and closure-state
      transition legality.

**Still open**
- [x] Expose real pre-change/candidate diff capture; real-mode approval is
      blocked until every device has a persisted diff, and the worker
      re-collects under lock before apply to detect stale baselines.
- [ ] Resolve custom-script write scopes from the signed/versioned manifest;
      current explicit scopes cover only the two built-in change types.
- [ ] Implement post-reconciliation device/NetBox verification; current
      reconciliation verifier remains a placeholder.
- [ ] Verify rollback with a fresh collection and compare against the
      persisted pre-change config hash.
- [ ] Production Batfish policy profiles and explicit unsupported/unknown
      analysis semantics.
- [ ] Native provider signature verification for GitLab/Bitbucket/Gitea and
      bind every CI signal to exact commit, PR and pipeline identity.

## Phase 3 — enterprise identity, tenancy and provisioning (P1)

**Implemented slices**
- [x] Browser bearer token is held in memory and is no longer written to
      localStorage or embedded as a build-time API token.
- [x] ZTP IP pool, DHCP backend and management interface are configurable;
      the enrolled MAC is passed into day-1 intent.

**Still open**
- [ ] Tenant/customer scope across all records, queries, audit, workflows,
      credentials and external resource mappings. This branch remains
      single-tenant; shared SaaS is not supported.
- [ ] OIDC, MFA enforcement, SCIM and browser-session revocation.
- [ ] HttpOnly/SameSite cookie or BFF session architecture. In-memory tokens
      reduce persistence exposure but do not prevent active XSS token theft.
- [ ] Central Vault credential resolution for every adapter and Git provider.
- [ ] Validated per-site/per-platform provisioning profiles; current ZTP
      values are global settings, not isolated site profiles.
- [ ] Per-adapter fake/real configuration and production-grade secret
      injection/rotation.

## Phase 4 — commercial operations and release engineering (P2)

**Implemented slices**
- [x] CI workflow includes frontend lint, test and production build jobs.
- [x] CI workflow includes a container image build gate.
- [x] Production-oriented real-adapter Compose profile added.
- [x] Security threat model, operations runbook, tenancy/identity gap register
      and release checklist added.

**Still open**
- [ ] Execute CI and fix all backend/frontend/type/lint/test failures.
- [ ] Migration upgrade/downgrade checks against production-like PostgreSQL.
- [ ] Ansible lint/syntax and runner contract tests.
- [ ] Full-stack smoke tests and multivendor adapter contract suites.
- [ ] Containerlab/EVE-NG lab acceptance tests for apply, verify and rollback.
- [ ] Backup/restore drill, API versioning, SLOs, audit export/retention,
      installation/upgrade automation and sizing guidance.
- [ ] Commercial packaging, licensing, support policy and independent
      penetration test.

## Acceptance gates

A phase is complete only when all applicable implementation items and tests
pass in CI, operational documentation exists, and no critical safety/security
finding remains open. The current branch is a hardening implementation slice,
not a production release. Do not merge or enable production network execution
until the P0 gates above have executable test evidence.
