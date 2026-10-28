#!/usr/bin/env bash
# Create (or update) the Secret personal-kg-sentry read by the MCP server and the backup job.
#
# Reads .env.sentry in the repo root (gitignored):
#   SENTRY_DSN=https://<key>@o<org>.ingest.<region>.sentry.io/<project>
#
# The Secret is optional in the manifests: without it error reporting stays off.
# Env vars are read at startup, so the server is restarted afterwards. Nothing secret is printed.
set -euo pipefail
cd "$(dirname "$0")/.."

env_file=.env.sentry
[[ -f $env_file ]] || { echo "$env_file not found (one line: SENTRY_DSN=...)" >&2; exit 1; }
chmod 600 "$env_file"

set -a
# shellcheck disable=SC1090
source "$env_file"
set +a
[[ ${SENTRY_DSN:-} == https://* ]] || { echo "SENTRY_DSN missing or not a https URL" >&2; exit 1; }

kubectl create secret generic personal-kg-sentry -n personal-kg \
  --from-literal=SENTRY_DSN="$SENTRY_DSN" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl rollout restart deploy/personal-kg-mcp -n personal-kg
kubectl rollout status deploy/personal-kg-mcp -n personal-kg --timeout=120s
echo "Sentry DSN set. The backup job picks it up on its next run."
