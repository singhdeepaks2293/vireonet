# VireoNet execution environment

Build the image that AWX runs VireoNet playbooks (and the Python runners)
in:

```bash
cd ansible/execution-environment
ansible-builder build -t vireonet-ee:latest
docker push <registry>/vireonet-ee:latest
```

Then register it in AWX as an execution environment named **"VireoNet EE"**
and point the `run-python` job template (and, when ready, the other
templates) at it.

## Pins

- ansible-core 2.17.7, ansible-runner 2.4.0 — pinned so the controller
  and the EE agree on the runner protocol.
- Nornir 3.5.0, Netmiko 4.5.0, NAPALM 5.0.0 — pinned versions from the
  E2 spec.

UNCERTAIN: Nornir 3 / NAPALM 5 / Netmiko 4 cross-compatibility is not
verified in this sandbox (no EE build here, and the runner tests run
against mocks). Verify on a real host with `ansible-builder build`
followed by a `run-python` dry-run against a lab device before trusting
the pins in production.
