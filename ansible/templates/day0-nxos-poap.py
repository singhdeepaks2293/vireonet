"""NX-OS POAP script — fetched by the switch, runs once at first boot.

Downloads the day-0 config for this serial from VireoNet and applies it.
No credentials in this script; the device authenticates with its one-time
token to the callback endpoint after boot, not to POAP.
"""
import urllib.request

SERIAL = "{{ serial }}"
HTTP_BASE = "{{ http_base_url }}"


def main():
    url = f"{HTTP_BASE}/{SERIAL}.cfg"
    with urllib.request.urlopen(url, timeout=30) as resp:
        cfg = resp.read().decode()
    with open("/tmp/day0.cfg", "w") as f:
        f.write(cfg)
    print(f"POAP: downloaded day-0 config for {SERIAL} ({len(cfg)} bytes)")


if __name__ == "__main__":
    main()
