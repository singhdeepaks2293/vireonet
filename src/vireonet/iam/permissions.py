from __future__ import annotations

ROLE_RANK = {"viewer": 1, "operator": 2, "approver": 3, "admin": 4}

ROLE_PERMISSIONS: dict[str, list[str]] = {
    "viewer": [
        "changes:read",
        "ztp:read",
        "locks:read",
        "health:read",
        "adapters:read",
        "approvals:read",
    ],
    "operator": [
        "changes:read",
        "ztp:read",
        "locks:read",
        "health:read",
        "adapters:read",
        "approvals:read",
        "changes:create",
        "changes:signal",
        "ztp:retry",
        "ztp:reset",
        "locks:release",
        "changes:rollback",
    ],
    "approver": [
        "changes:read",
        "ztp:read",
        "locks:read",
        "health:read",
        "adapters:read",
        "approvals:read",
        "changes:create",
        "changes:signal",
        "ztp:retry",
        "ztp:reset",
        "locks:release",
        "changes:rollback",
        "approvals:approve",
    ],
    "admin": [
        "changes:read",
        "ztp:read",
        "locks:read",
        "health:read",
        "adapters:read",
        "approvals:read",
        "changes:create",
        "changes:signal",
        "ztp:retry",
        "ztp:reset",
        "locks:release",
        "changes:rollback",
        "approvals:approve",
        "iam:manage",
    ],
}

ALL_PERMISSIONS = sorted({p for perms in ROLE_PERMISSIONS.values() for p in perms})


def has_permission(role: str, permission: str) -> bool:
    return permission in ROLE_PERMISSIONS.get(role, [])


def can(role: str, minimum: str) -> bool:
    return ROLE_RANK.get(role, 0) >= ROLE_RANK.get(minimum, 0)
