"""F1 real diff: apply_intent_to_config + compute_diff, checked against what
the applier actually produces (no pre-written expected counts)."""

from __future__ import annotations

from vireonet.diff import apply_intent_to_config, compute_diff, config_hash, normalize_config

RUNNING = """hostname leaf-01
!
vlan 1
 name default
!
interface Ethernet1/1
 switchport mode access
 switchport access vlan 1
!
"""

VLAN_ADD = {
    "change_type": "vlan_add",
    "vlan_id": 100,
    "vlan_name": "web",
    "interfaces": ["Ethernet1/1"],
    "devices": ["leaf-01"],
}


def test_vlan_add_diff_counts_come_from_the_applier() -> None:
    candidate = apply_intent_to_config(RUNNING, VLAN_ADD)
    diff = compute_diff("leaf-01", RUNNING, candidate)
    added = [r["text"] for r in diff["lines"] if r["type"] == "add"]
    removed = [r["text"] for r in diff["lines"] if r["type"] == "remove"]
    # "switchport mode access" is already present, so it is context, not an add.
    assert added == [" switchport access vlan 100", "vlan 100", " name web"]
    assert removed == [" switchport access vlan 1"]
    assert diff["added"] == len(added) == 3
    assert diff["removed"] == len(removed) == 1
    assert diff["is_empty"] is False
    assert diff["unified"][0] == "--- a/leaf-01.cfg"
    assert diff["unified"][1] == "+++ b/leaf-01.cfg"
    assert diff["running_hash"] == config_hash(RUNNING)
    assert diff["candidate_hash"] == config_hash(candidate)


def test_vlan_remove_shows_removals_not_additions() -> None:
    candidate = apply_intent_to_config(RUNNING, VLAN_ADD)
    removed_candidate = apply_intent_to_config(
        candidate, {"change_type": "vlan_remove", "vlan_id": 100}
    )
    diff = compute_diff("leaf-01", candidate, removed_candidate)
    assert diff["added"] == 0
    assert diff["removed"] == 3
    assert sorted(r["text"] for r in diff["lines"] if r["type"] == "remove") == sorted(
        ["vlan 100", " name web", " switchport access vlan 100"]
    )
    assert "vlan 100" not in removed_candidate


def test_noop_change_is_empty() -> None:
    candidate = apply_intent_to_config(RUNNING, VLAN_ADD)
    diff = compute_diff("leaf-01", candidate, apply_intent_to_config(candidate, VLAN_ADD))
    assert diff["is_empty"] is True
    assert diff["added"] == 0 and diff["removed"] == 0
    assert all(r["type"] == "context" for r in diff["lines"])


def test_static_route_remove_and_acl_entry_remove() -> None:
    running = (
        "hostname leaf-01\nip route 10.0.0.0/24 192.168.1.1 1\nip route 10.1.0.0/24 192.168.1.1 1\n"
    )
    diff = compute_diff(
        "leaf-01",
        running,
        apply_intent_to_config(
            running,
            {
                "change_type": "static_route_remove",
                "prefix": "10.0.0.0/24",
                "next_hop": "192.168.1.1",
            },
        ),
    )
    assert (diff["added"], diff["removed"]) == (0, 1)
    assert "10.1.0.0/24" in diff["lines"][0]["text"] or any(
        "10.1.0.0/24" in r["text"] for r in diff["lines"] if r["type"] == "context"
    )

    acl_running = "ip access-list extended WEB\n 10 permit tcp any any eq 80\n 20 deny ip any any\n"
    diff = compute_diff(
        "leaf-01",
        acl_running,
        apply_intent_to_config(
            acl_running,
            {
                "change_type": "acl_entry_remove",
                "acl_name": "WEB",
                "sequence": 10,
            },
        ),
    )
    assert (diff["added"], diff["removed"]) == (0, 1)
    assert [r["text"] for r in diff["lines"] if r["type"] == "remove"] == [
        " 10 permit tcp any any eq 80"
    ]


def test_acl_entry_add_is_idempotent() -> None:
    running = "ip access-list extended WEB\n 10 permit tcp any any eq 80\n"
    intent = {
        "change_type": "acl_entry_add",
        "acl_name": "WEB",
        "action": "deny",
        "protocol": "udp",
        "source": "any",
        "destination": "10.0.0.0/24",
        "destination_port": 53,
    }
    once = apply_intent_to_config(running, intent)
    assert compute_diff("leaf-01", running, once)["added"] == 1
    assert compute_diff("leaf-01", once, apply_intent_to_config(once, intent))["is_empty"] is True


def test_interface_set_and_ztp_device() -> None:
    running = "interface Ethernet1/1\n no shutdown\n"
    candidate = apply_intent_to_config(
        running,
        {
            "change_type": "interface_set",
            "interface": "Ethernet1/1",
            "description": "uplink",
            "enabled": False,
            "mtu": 9000,
        },
    )
    assert " shutdown\n" in candidate and " no shutdown" not in candidate
    assert " description uplink" in candidate and " mtu 9000" in candidate

    candidate = apply_intent_to_config(
        "hostname old\n", {"change_type": "ztp_device", "devices": ["leaf-99"]}
    )
    assert candidate == "hostname leaf-99\n"


def test_large_config_diff_is_correct() -> None:
    parts = ["hostname leaf-01"]
    for i in range(1, 1001):
        parts += [
            f"interface Ethernet1/{i}",
            " switchport mode access",
            " switchport access vlan 1",
        ]
    running = "\n".join(parts) + "\n"
    intent = dict(VLAN_ADD, interfaces=["Ethernet1/500"])
    candidate = apply_intent_to_config(running, intent)
    diff = compute_diff("leaf-01", running, candidate)
    assert (diff["added"], diff["removed"]) == (3, 1)
    assert " switchport access vlan 100" in candidate
    # Only Ethernet1/500 changed: every other interface still has vlan 1.
    # (Count full lines: "vlan 100" contains "vlan 1" as a substring.)
    assert candidate.count(" switchport access vlan 1\n") == 999
    assert candidate.count(" switchport access vlan 100\n") == 1


def test_normalize_hash_stability() -> None:
    assert normalize_config("a\r\nb  \n\n\n") == "a\nb\n"
    assert normalize_config("") == ""
    assert config_hash("a\r\nb  \n") == config_hash("a\nb\n")
    assert config_hash("a\nb\n") != config_hash("a\nc\n")


def test_unknown_change_type_is_noop() -> None:
    assert (
        compute_diff("leaf-01", RUNNING, apply_intent_to_config(RUNNING, {"change_type": "nope"}))[
            "is_empty"
        ]
        is True
    )
