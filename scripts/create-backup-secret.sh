#!/usr/bin/env bash
# Create (or update) the Secret personal-kg-backup used by k8s/backup-cronjob.yaml.
#
# Reads .env.backup in the repo root (gitignored):
#   S3_BUCKET, S3_ACCESS_KEY_ID (B2 keyID), S3_SECRET_ACCESS_KEY (B2 applicationKey)
#   BACKUP_PASSPHRASE  — generated here on first run if missing
#
# Without BACKUP_PASSPHRASE the backups cannot be decrypted. Keep a copy outside
# this Mac (password manager). Nothing secret is printed.
set -euo pipefail
cd "$(dirname "$0")/.."

env_file=.env.backup
[[ -f $env_file ]] || { echo "$env_file not found" >&2; exit 1; }
command -v rclone >/dev/null || { echo "rclone is required: brew install rclone" >&2; exit 1; }

if ! grep -q '^BACKUP_PASSPHRASE=' "$env_file"; then
  passphrase=$(openssl rand -base64 48 | tr -d '/+=\n' | cut -c1-40)
  printf '\n# rclone crypt passphrase. Keep a copy in a password manager.\nBACKUP_PASSPHRASE=%s\n' \
    "$passphrase" >>"$env_file"
  echo "Generated BACKUP_PASSPHRASE in $env_file — copy it to your password manager."
fi
chmod 600 "$env_file"

set -a
# shellcheck disable=SC1090
source "$env_file"
set +a

kubectl create secret generic personal-kg-backup -n personal-kg \
  --from-literal=B2_BUCKET="$S3_BUCKET" \
  --from-literal=B2_KEY_ID="$S3_ACCESS_KEY_ID" \
  --from-literal=B2_APPLICATION_KEY="$S3_SECRET_ACCESS_KEY" \
  --from-literal=BACKUP_PASSWORD_OBSCURED="$(rclone obscure "$BACKUP_PASSPHRASE")" \
  --dry-run=client -o yaml | kubectl apply -f -

echo "Secret keys: $(kubectl get secret -n personal-kg personal-kg-backup \
  -o go-template='{{range $k, $v := .data}}{{$k}} {{end}}')"
