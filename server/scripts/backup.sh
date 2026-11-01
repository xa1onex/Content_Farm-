#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
umask 077
mkdir -p backups
stamp=$(date -u +%Y%m%dT%H%M%SZ)
docker compose exec -T db pg_dump -U shortpost_owner -d shortpost -Fc > "backups/shortpost-$stamp.dump.tmp"
test -s "backups/shortpost-$stamp.dump.tmp"
docker compose exec -T db pg_restore --list < "backups/shortpost-$stamp.dump.tmp" >/dev/null
mv "backups/shortpost-$stamp.dump.tmp" "backups/shortpost-$stamp.dump"
# No automatic deletion of backups. Add reviewed retention/off-server copying in operations.
