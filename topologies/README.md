# Lab topologies

Two backends, two topology formats, one lab: two leaf switches
(`leaf-01` / `leaf-02`) linked to each other.

| File | Backend | Nodes | Images |
|------|---------|-------|--------|
| `two-leaf.unl` | EVE-NG | leaf-01, leaf-02 (`viosl2` qemu) | `vios_l2-adventerprisek9-m.qcow2` (IOSvL2) |
| `two-leaf.clab.yml` | Containerlab | leaf-01, leaf-02 (`cisco_iol`) | `vrnetlab/cisco_iol:latest` |

## Node kinds and images per backend

### EVE-NG (`two-leaf.unl`)

- Kind/template: `viosl2` (IOSvL2, qemu). EVE-NG is the only backend
  old IOS images are known to run on — the `.unl` uses the IOSvL2 qemu
  image installed on the EVE-NG host. Image names differ between EVE-NG
  installs; adjust the `image` attribute before importing.

### Containerlab (`two-leaf.clab.yml`)

- Kinds known to work with this lab shape: `cisco_iol`
  (`vrnetlab/cisco_iol:latest`), `srl` (Nokia SR Linux),
  `ceos` (Arista cEOS), `crpd` (Juniper cRPD).
- **Old IOS on Containerlab is UNCERTAIN / kind-dependent**: whether an
  old IOS image runs at all depends on the node kind — `cisco_iol` runs
  IOS via vrnetlab, but there is no generic "old IOS" kind. Do not assume
  an EVE-NG IOSvL2 image works under Containerlab.

## Running the smoke test

```bash
python scripts/lab_smoke.py --backend eveng --topology topologies/two-leaf.unl --lab two-leaf
python scripts/lab_smoke.py --backend containerlab --topology topologies/two-leaf.clab.yml --lab two-leaf
```

Deploy -> wait ready -> `show version` on leaf-01 -> destroy, always in a
`finally`. See `docs/LAB.md`.
