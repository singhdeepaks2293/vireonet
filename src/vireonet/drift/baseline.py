"""Shared baseline commit (D1).

One function for every path that stores a running config as the new
baseline and commits it to the device repo: E3 reconciliation and ZTP
day-1. Idempotent per (device, normalised hash, message): committing
the same config with the same message twice creates one Git commit and
returns the first SHA. A different message is a different commit even
when the config is unchanged — that is what keeps E3 reconciliation's
behaviour (one commit per reconcile job, each naming its job/script)
unchanged while the shared function stays idempotent for true repeats.

The "already committed" cache is scoped per DeviceRepoManager instance
(a WeakKeyDictionary): two managers backed by different repos must
never share SHAs, and a fresh manager starts with an empty cache.
"""

from __future__ import annotations

import weakref

from vireonet.config_store.device_repos import DeviceRepoManager
from vireonet.config_store.store import ConfigStore
from vireonet.diff import config_hash, normalize_config

# Per-manager cache of the last Git SHA committed per
# (device, content hash, message). The durable record is the ConfigStore
# version (which dedupes by hash); the cache only lets an identical
# re-commit return the same SHA instead of committing again.
_LAST_BASELINE_SHA: weakref.WeakKeyDictionary[
    DeviceRepoManager, dict[tuple[str, str, str], str]
] = weakref.WeakKeyDictionary()


async def commit_baseline(
    *,
    device: str,
    running_config: str,
    ticket: str,
    config_store: ConfigStore,
    device_repos: DeviceRepoManager,
    message: str | None = None,
) -> str:
    """Store + commit the running config as the device baseline.

    Returns the Git commit SHA. Committing the same normalised config
    with the same message twice returns the first SHA and creates no
    second commit; a different message always commits (see module docstring).
    """
    normalized = normalize_config(running_config)
    digest = config_hash(normalized)
    resolved_message = message if message is not None else f"{ticket}: baseline for {device}"
    key = (device, digest, resolved_message)
    cache = _LAST_BASELINE_SHA.setdefault(device_repos, {})
    cached = cache.get(key)
    if cached is not None:
        return cached
    await config_store.store_running(device, normalized, ticket_number=ticket, source="baseline")
    sha = await device_repos.commit_config(
        hostname=device,
        ticket_number=ticket,
        content=normalized,
        message=resolved_message,
    )
    cache[key] = sha
    return sha


def _clear_baseline_cache() -> None:
    """Tests only: forget cached SHAs so a fresh commit is exercised."""
    _LAST_BASELINE_SHA.clear()
