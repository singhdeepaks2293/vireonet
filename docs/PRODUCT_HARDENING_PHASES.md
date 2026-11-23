# VireoNet product hardening — phases 1–4

This plan tracks the commercial-product hardening work. Changes are made on
`product-hardening/phases-1-4`; `main` is not modified directly.

## Delivery rules

- Preserve the current FastAPI + Temporal + PostgreSQL + NetBox + AWX architecture.
- Treat network execution as safety-critical: unknown outcomes are failures or
  pending states, never success.
- Each phase requires unit tests, integration tests where adapters are involved,
  migration checks where schema changes, and documented operational recovery.
- Do not claim production readiness based on fake-adapter tests alone.
- Keep secrets out of workflow payloads, logs, frontend storage, and job output.

## Phase 1 — execution safety and recovery (P0)

- [x] Require operator role to register ZTP devices; require viewer role for
      serial lookup. Device callback remains token-authenticated.
- [x] Only transition a change to `rolled_back` when rollback activity returns
      `ok=true`; unknown/failed rollback outcomes transition to `failed`.
- [x] Only return ZTP workflow state `active` when activation confirms success.
- [ ] Persist backup job references and artifact metadata in PostgreSQL; remove
      worker-process dictionaries as the source of rollback truth.
- [ ] Add AWX launch idempotency/reconciliation so activity retries cannot
      create duplicate network changes after a launch timeout.
- [ ] Keep device locks held on failed/unknown rollback; add lease renewal,
      ownership/fencing and a recovery path for stale locks.
- [ ] ServiceNow closure failure must remain visible as `closure_pending`;
      do not report internal closure as successful while ITSM is open.
- [ ] Add tests for worker restart/replay, duplicate activity delivery,
      rollback failure, rollback timeout, ZTP authorization and activation failure.

## Phase 2 — change assurance and reconciliation (P1)

- [ ] Capture real pre-change and candidate config snapshots and persist
      per-device diffs before approval; show exact added/removed lines.
- [ ] Verify collect-state success and completeness before baseline commits.
- [ ] Resolve declared-write scopes from the signed/versioned script manifest;
      reject unknown or out-of-scope writes.
- [ ] Implement post-reconciliation verification against device and NetBox state.
- [ ] Add policy profiles so production Batfish checks block where supported;
      explicitly report unsupported analyses as unknown, not pass.
- [ ] Verify rollback with a fresh collection and compare to the pre-change hash.
- [ ] Verify provider-native webhook signatures and bind CI status to the exact
      commit / PR / pipeline identity.

## Phase 3 — enterprise identity, tenancy and provisioning (P1)

- [ ] Add tenant/customer scope to all persisted domain objects, queries,
      audit records, jobs, credentials and external resource mappings before
      enabling multi-customer SaaS.
- [ ] Add OIDC integration, MFA policy support, session revocation and
      least-privilege authorization; preserve local auth for isolated installs.
- [ ] Replace browser localStorage bearer-token persistence with a secure
      HttpOnly/SameSite session or a documented BFF/token design.
- [ ] Centralize adapter credential resolution through Vault; redact secrets
      from structured logs, workflow inputs and AWX extra-vars.
- [ ] Replace hardcoded ZTP pool, DHCP backend, management interface and
      placeholder MAC with validated site/platform provisioning profiles.
- [ ] Add per-adapter fake/real mode and production Compose/Kubernetes
      configuration that does not silently force fake adapters.

## Phase 4 — commercial operations and release engineering (P2)

- [ ] CI: backend unit/type/lint tests, frontend build/lint/tests, migration
      upgrade/downgrade checks, Ansible lint/syntax, container build and
      full-stack smoke tests.
- [ ] Add multivendor contract tests and Containerlab/EVE-NG lab acceptance
      suites with explicit capability declarations.
- [ ] Add API versioning/deprecation policy, migration/upgrade tooling,
      backup/restore drills and supported rollback procedure.
- [ ] Add observability SLOs, workflow queue health, stuck-change alerts,
      adapter health, audit export and retention controls.
- [ ] Add install guide, production deployment profiles, sizing guidance,
      security threat model, operator runbooks and release checklist.
- [ ] Define commercial packaging boundaries: on-prem/hybrid first, tenant
      isolation before SaaS, licensing and support policy.

## Acceptance gates

A phase is complete only when all checkboxes in that phase are implemented,
tests pass in CI, the relevant operational documentation exists, and no
critical safety/security finding remains open. A code change without an
executable test or a stated reason it cannot be tested is not considered done.
