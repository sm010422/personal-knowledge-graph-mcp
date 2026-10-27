# Source this to point a local rclone at the same encrypted remote the CronJob uses:
#   source scripts/backup-rclone-env.sh
#   rclone ls backup:                                # list backups (names are timestamps)
#   rclone copy backup:neo4j-<ts>.jsonl.gz backups/  # download + decrypt (backups/ is gitignored)
# Reads .env.backup in the repo root. Mirrors the env block in k8s/backup-cronjob.yaml.
_repo_root="$(cd "$(dirname "${BASH_SOURCE[0]:-${(%):-%x}}")/.." && pwd)"
set -a
# shellcheck disable=SC1091
source "$_repo_root/.env.backup"
set +a
export RCLONE_CONFIG_B2_TYPE=b2
export RCLONE_CONFIG_B2_ACCOUNT="$S3_ACCESS_KEY_ID"
export RCLONE_CONFIG_B2_KEY="$S3_SECRET_ACCESS_KEY"
export RCLONE_CONFIG_B2_HARD_DELETE=true
export RCLONE_CONFIG_BACKUP_TYPE=crypt
export RCLONE_CONFIG_BACKUP_REMOTE="b2:$S3_BUCKET/neo4j"
export RCLONE_CONFIG_BACKUP_FILENAME_ENCRYPTION=off
export RCLONE_CONFIG_BACKUP_DIRECTORY_NAME_ENCRYPTION=false
RCLONE_CONFIG_BACKUP_PASSWORD="$(rclone obscure "$BACKUP_PASSPHRASE")"
export RCLONE_CONFIG_BACKUP_PASSWORD
unset _repo_root
