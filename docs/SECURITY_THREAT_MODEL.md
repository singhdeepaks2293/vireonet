# VireoNet security threat model

## Assets

- Network device configuration and management-plane access
- AWX credentials, NetBox/ServiceNow tokens, Git credentials and Vault secrets
- Change intent, approvals, CI evidence, workflow history and audit records
- ZTP enrollment tokens, device serial/MAC/IP identity and bootstrap artifacts
- Configuration backups, diffs and Git configuration repositories

## Trust boundaries

1. Browser to API: JWT authentication and role authorization. Browser bearer
   tokens are now held in memory rather than persisted in localStorage. This
   reduces persistent token exposure but does not protect against active XSS.
2. API/webhook ingress: GitHub uses native `X-Hub-Signature-256`; ServiceNow
   uses the configured VireoNet HMAC/bearer contract; GitLab uses its shared
   token header. Provider-specific Bitbucket/Gitea signature verification is
   not yet implemented.
3. API/Temporal: workflow inputs must contain identifiers and non-secret
   intent only. Temporal histories are durable and must not be treated as a
   secret store.
4. Worker/AWX: AWX is the network execution boundary. Network credentials
   should remain in AWX credential objects/Execution Environments or Vault,
   never in browser payloads or VireoNet workflow arguments.
5. VireoNet/external systems: NetBox and ServiceNow closure are external
   transactions. A failure leaves an explicit pending state and requires
   reconciliation rather than optimistic success.

## Controls implemented in this hardening branch

- Role checks on ZTP registration and serial lookup; callback authentication
  remains a single-use, expiring token.
- GitHub-native HMAC signature verification.
- NetBox identity lookup required before local ZTP enrollment.
- Duplicate ZTP registration cannot silently mint a fresh callback token.
- Single-use callback token verification uses a database row lock.
- Rollback is only reported as successful when the rollback activity confirms
  success; failed/unknown rollback retains locks and backup references.
- AWX backup job references are persisted in the change JSON record before
  apply, so worker restart does not erase the rollback job IDs.
- Device lock leases can be renewed by their current ticket owner; apply and
  rollback check lock ownership before execution.
- NetBox/ServiceNow closure failure becomes `closure_pending`; approver-only
  retry is available.
- Failed or empty post-change state collection cannot be committed as a new
  baseline.
- Browser access tokens are not persisted in localStorage or embedded as
  build-time API tokens.

## Remaining high-risk controls

- AWX launch idempotency is not solved. A timeout after AWX accepted an apply
  request can still leave an uncertain launch. Operators must inspect AWX
  before retrying. Do not treat retries as exactly-once.
- Lock renewal is explicit at workflow boundaries, not a continuous Temporal
  heartbeat/fencing protocol. A very long AWX job can outlive a lease; use
  conservative TTLs and monitor long-running jobs.
- Tenant isolation is not implemented. Treat this release as single-customer,
  single-tenant only; do not host mutually untrusted customers in one database.
- OIDC, MFA enforcement, session revocation for browser sessions, and
  centralized Vault credential resolution are not complete.
- GitHub is the only provider with native signature verification in this
  branch; Bitbucket/Gitea still use the generic shared webhook verification
  contract.
- Login rate limiting, security headers/CSP, dependency scanning and formal
  penetration testing remain required before internet exposure.
- Multi-site ZTP provisioning profiles are not implemented. Current settings
  make the pool, DHCP server and management interface configurable globally;
  validate each site manually and do not assume per-site isolation.

## Production rules

- Never enable the real adapter mode with default secrets.
- Do not expose PostgreSQL, Temporal or AWX APIs to the public internet.
- Use least-privilege AWX roles, short-lived Vault credentials and separate
  credentials per environment.
- Restrict who can approve, execute, retry provisioning, release locks and
  retry external closure.
- Keep immutable audit evidence and alert on failed/unknown rollback, expired
  locks, closure_pending, reconciliation_pending and ZTP activation failure.
