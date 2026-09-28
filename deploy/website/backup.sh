#!/bin/sh
set -eu
cd /opt/thebitstoday/app
backup_dir=/opt/thebitstoday/backups
install -d -m 700 "$backup_dir"
umask 077
target="$backup_dir/thebitstoday-$(date -u +%Y%m%dT%H%M%SZ).dump"
docker compose --env-file deploy/website/.env -f deploy/website/compose.yml exec -T db pg_dump -U thebitstoday -d thebitstoday -Fc > "$target.partial"
mv "$target.partial" "$target"
# No automatic deletion: keep backups until an off-server retention policy is set.
echo "Database backup completed: $target"
