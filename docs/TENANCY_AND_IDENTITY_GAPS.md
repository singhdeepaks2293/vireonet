# Tenant and identity architecture gate

VireoNet's current domain model is single-tenant. It does not yet provide
tenant IDs and tenant-scoped query enforcement across changes, audit records,
device locks, ZTP devices, config versions, drift records, credentials and
external resource mappings.

## Decision

Do not deploy this branch as a shared SaaS service for multiple customers.
A separate database per customer is the safest interim isolation boundary for
hosted pilots. Single-customer on-prem/hybrid deployments may proceed only
after the operational/security checklist is satisfied.

## Required implementation before multi-tenant SaaS

1. Add a Tenant entity and immutable tenant ID to every customer-owned row.
2. Require tenant context at authentication and derive it from trusted identity
   claims, never from a request body or browser-controlled header.
3. Apply tenant filters in every repository/service query and background
   activity; enforce database row-level security where practical.
4. Scope uniqueness constraints (ticket, serial, device lock, config version,
   user identity) by tenant where semantics require it.
5. Scope AWX inventories/credentials, NetBox tenants/sites, Git repositories,
   Vault paths, Temporal workflow IDs and webhook routing to the tenant.
6. Add cross-tenant negative tests for every API and workflow.
7. Define tenant data export/deletion, retention, backup/restore and audit
   policies.
8. Perform an independent security review before enabling shared tenancy.

## Identity

Local username/password IAM remains the supported implementation in this
branch. OIDC federation, MFA enforcement, SCIM provisioning and conditional
access are not implemented. Do not advertise SSO/MFA support until the
protocol integration and end-to-end tests are delivered.
