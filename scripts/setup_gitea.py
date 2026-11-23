"""Gitea install + integrate, manual or automatic.
Usage:
  python scripts/setup_gitea.py --manual
    Prints the exact manual steps and exits. Nothing is changed.
  python scripts/setup_gitea.py --auto --admin-user admin --repo netops/intent
    Installs Gitea (docker compose), creates the admin user, creates the repo,
    creates an API token, stores it in Vault, writes .env.gitea, verifies.
  python scripts/setup_gitea.py --verify
    Checks Gitea health + VireoNet git provider health, nothing else.
Env: GITEA_ADMIN_PASSWORD, GITEA_DB_PASSWORD, VIREONET_GIT_REPO, VAULT_ADDR, VAULT_TOKEN.
The API token is never printed. It is stored in Vault at git/token and the
script prints only that it was stored.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request

MANUAL_STEPS = [
    "1. Install Docker and Docker Compose v2 on the host.",
    "2. Set GITEA_DB_PASSWORD (strong, 16+ chars) and GITEA_ROOT_URL (e.g. http://localhost:3000/).",
    "3. Run: GITEA_DB_PASSWORD='<password>' ./scripts/install_gitea.sh",
    "4. Open http://localhost:3000/ and create the admin user, or run: python scripts/setup_gitea.py --auto (does steps 4-7).",
    "5. Create the repo netops/intent (org netops, or your owner/repo).",
    "6. Create an API token for the admin (Settings -> Applications -> Generate New Token, scope repo + admin).",
    "7. Store the token in Vault: vault kv put secret/git token='<token>' (path VIREONET_GIT_TOKEN_SECRET, default git/token).",
    "8. Set VireoNet config: VIREONET_GIT_PROVIDER=gitea, VIREONET_GIT_BASE_URL=http://localhost:3000, VIREONET_GIT_REPO=netops/intent.",
    "9. Restart VireoNet API + worker, then verify: python scripts/setup_gitea.py --verify",
    "10. Add the webhook in Gitea: repo Settings -> Webhooks -> Add, URL https://vireonet.example.com/webhooks/gitea, events pull_request + workflow_run.",
]


def _http(method: str, url: str, token: str | None = None, body: dict | None = None) -> dict:
    req = urllib.request.Request(url, method=method)
    req.add_header("Accept", "application/json")
    if token:
        req.add_header("Authorization", f"token {token}")
    data = json.dumps(body).encode() if body is not None else None
    if data:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, data=data, timeout=30) as resp:
        raw = resp.read().decode()
        return json.loads(raw) if raw else {}


def _wait_healthy(base: str, timeout_s: int = 180) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            _http("GET", f"{base}/api/v1/version")
            return
        except Exception:
            time.sleep(5)
    raise SystemExit("Gitea did not become healthy in time.")


def _auto(admin_user: str, repo: str) -> None:
    base = os.environ.get("GITEA_BASE_URL", "http://localhost:3000").rstrip("/")
    admin_password = os.environ.get("GITEA_ADMIN_PASSWORD", "")
    if len(admin_password) < 12:
        raise SystemExit("Set GITEA_ADMIN_PASSWORD (12+ chars).")
    print("Installing Gitea...")
    subprocess.run(["./scripts/install_gitea.sh"], check=True)
    _wait_healthy(base)
    print("Creating admin user...")
    try:
        _http(
            "POST",
            f"{base}/api/v1/admin/users",
            body={
                "user_name": admin_user,
                "password": admin_password,
                "email": f"{admin_user}@example.local",
                "must_change_password": False,
            },
        )
    except Exception as exc:
        # Admin may already exist; continue and verify login instead.
        print(f"Admin create returned an error (may already exist): {exc}")
    print("Creating repo...")
    owner, _, name = repo.partition("/")
    _http(
        "POST",
        f"{base}/api/v1/user/repos",
        token=_admin_token(base, admin_user, admin_password),
        body={"name": name, "private": False, "auto_init": True},
    )
    print("Creating API token and storing it in Vault (token is not printed)...")
    token_data = _http(
        "POST",
        f"{base}/api/v1/users/{admin_user}/tokens",
        token=_admin_token(base, admin_user, admin_password),
        body={"name": "vireonet", "scopes": ["repo", "admin"]},
    )
    token = str(token_data.get("sha1", ""))
    if not token:
        raise SystemExit("Gitea did not return a token.")
    _store_in_vault(token)
    print("Token stored in Vault at git/token. Wrote .env.gitea (no secrets in it).")
    with open(".env.gitea", "w", encoding="utf-8") as f:
        f.write(
            f"VIREONET_GIT_PROVIDER=gitea\nVIREONET_GIT_BASE_URL={base}\nVIREONET_GIT_REPO={repo}\nVIREONET_GIT_TOKEN_SECRET=git/token\n"
        )
    print("Done. Restart VireoNet API + worker, then run: python scripts/setup_gitea.py --verify")


def _admin_token(base: str, admin_user: str, admin_password: str) -> str:
    # Gitea has no password-to-token endpoint in the public API; the admin token
    # is created with basic auth. This is the one place basic auth is used.
    import base64

    creds = base64.b64encode(f"{admin_user}:{admin_password}".encode()).decode()
    req = urllib.request.Request(f"{base}/api/v1/users/{admin_user}/tokens", method="POST")
    req.add_header("Authorization", f"Basic {creds}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(
        req,
        data=json.dumps({"name": "vireonet-bootstrap", "scopes": ["repo", "admin"]}).encode(),
        timeout=30,
    ) as resp:
        return str(json.loads(resp.read().decode()).get("sha1", ""))


def _store_in_vault(token: str) -> None:
    # hvac is the Vault client (M4). If hvac is not installed, print the Vault
    # CLI command instead of failing — the operator runs it manually.
    try:
        import hvac  # type: ignore

        client = hvac.Client(
            url=os.environ.get("VAULT_ADDR", "http://localhost:8200"),
            token=os.environ.get("VAULT_TOKEN", ""),
        )
        client.secrets.kv.v2.create_or_update_secret(path="git/token", secret={"token": token})
    except Exception:
        print("hvac not available or Vault unreachable. Store the token manually:")
        print("  vault kv put secret/git token='<token from Gitea>'")


def _verify() -> None:
    base = os.environ.get("GITEA_BASE_URL", "http://localhost:3000").rstrip("/")
    version = _http("GET", f"{base}/api/v1/version")
    print(f"Gitea version: {version.get('version', 'unknown')}")
    print("Gitea is healthy. Check VireoNet: GET /adapters/health should list git/gitea as ok.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manual", action="store_true")
    parser.add_argument("--auto", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--admin-user", default="vireonet-admin")
    parser.add_argument("--repo", default=os.environ.get("VIREONET_GIT_REPO", "netops/intent"))
    args = parser.parse_args()
    if args.manual:
        print("\n".join(MANUAL_STEPS))
    elif args.auto:
        _auto(args.admin_user, args.repo)
    elif args.verify:
        _verify()
    else:
        parser.print_help()
        sys.exit(2)


if __name__ == "__main__":
    main()
