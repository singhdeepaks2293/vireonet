#!/usr/bin/env bash
# Install Gitea with Docker Compose. Usage:
#   GITEA_DB_PASSWORD='<strong>' GITEA_ROOT_URL=http://localhost:3000/ ./scripts/install_gitea.sh
# Exits non-zero with a plain message if Docker / Compose is missing.
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is not installed. Install Docker first, then re-run this script." >&2
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose v2 is not available. Install it, then re-run this script." >&2
  exit 1
fi
if [ -z "${GITEA_DB_PASSWORD:-}" ]; then
  echo "Set GITEA_DB_PASSWORD before running this script." >&2
  exit 1
fi

docker compose -f deployments/gitea/docker-compose.gitea.yml up -d
echo "Waiting for Gitea to become healthy (up to 3 minutes)..."
for i in $(seq 1 18); do
  if curl -fsS http://localhost:3000/api/v1/version >/dev/null 2>&1; then
    echo "Gitea is up at http://localhost:3000/"
    exit 0
  fi
  sleep 10
done
echo "Gitea did not become healthy in 3 minutes. Check: docker compose -f deployments/gitea/docker-compose.gitea.yml logs gitea" >&2
exit 1
