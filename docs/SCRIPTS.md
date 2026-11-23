# Custom scripts in VireoNet (E3)

Custom scripts are Python programs that run on devices through AWX
(`ansible/playbooks/run_python.yml`, job_type `python`). They are the only
way to run code that is not a shipped playbook — and they are deliberately
narrow: a script may only run if it is listed in `policies/scripts.yaml`
at exactly the listed SHA, for an allowed change type and platform.

## Contract

A script:

- reads one JSON `StepRequest` from stdin — never from files, never from
  the environment (no secrets are passed on the command line or in
  `extra_vars`);
- writes one JSON `StepResult` to stdout, wrapped in the
  `VIREONET-RESULT-BEGIN` / `VIREONET-RESULT-END` markers;
- exits 0 only on success, non-zero on any failure;
- makes **no change** when `dry_run` is true (a dry-run that changes
  something is a bug, and the apply hash check below will catch a
  mismatch);
- is idempotent: running it twice has the same effect as running it once;
- declares **every** change it made (or would make) in `declared_changes`,
  complete: `{object_type, identifier, field, old_value, new_value,
  config_lines}` — an empty or out-of-scope declaration fails the step;
- logs to stderr, never to stdout (stdout is the result channel);
- never prints credentials.

## Approval flow

1. Dry-run the script. VireoNet records `dry_run_hash(result)` — a SHA-256
   of the dry-run's output + declared changes.
2. An approver approves that hash.
3. Apply runs the same script at the same SHA. If the apply's hash differs
   from the approved dry-run hash, the apply is refused
   ("apply output differs from the approved dry-run").

## Reconciliation

A script never writes to Git or NetBox itself — it has no access. After
post-check, VireoNet reconciles: the running config is stored and
committed to the device repo, and declared changes inside the manifest
`writes` scope are applied to NetBox. A declared change outside the scope
leaves the change `reconciliation_pending` with a drift record.

## Template

```python
import json
import sys

from vireonet_runners.common import emit, read_request
from vireonet_runners.types import RunnerResult

def main(step: str) -> int:
    req = read_request()
    req["step"] = step
    # ... do the work (or nothing, when req["dry_run"] is True) ...
    result = RunnerResult(
        status="successful",
        step=step,
        device=req["device"],
        output={},
        declared_changes=[
            {
                "object_type": "interface",
                "identifier": "GigabitEthernet0/1",
                "field": "description",
                "old_value": "",
                "new_value": "uplink to core",
                "config_lines": ["interface GigabitEthernet0/1", " description uplink to core"],
            }
        ],
    )
    emit(result.model_dump())
    return 0

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
```

## Example: set_interface_description

Sets (or clears) the description on one interface, and optionally its
shutdown state. Manifest scope: `interface.description`,
`interface.enabled`. Change type `interface_set`; platforms `ios`,
`iosxe`, `nxos`. The dry-run reports the description it would set and
declares the change; the apply sends exactly those lines.
