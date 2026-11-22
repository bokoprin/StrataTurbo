#!/usr/bin/env bash
set -euo pipefail

BACKUP_DIR="/home/shinsuke/StrataTurbo/backups"
PROFILE_DST="/home/shinsuke/strata-data/config/strata-ud-iq3_xxs.json"
SERVICE_DST="/home/shinsuke/.config/systemd/user/strata-llm.service"

if [[ ! -f "$BACKUP_DIR/strata-ud-iq3_xxs.json.baseline" ]] || [[ ! -f "$BACKUP_DIR/strata-llm.service.baseline" ]]; then
  echo "ERROR: Baseline backup files not found in $BACKUP_DIR" >&2
  exit 1
fi

if [[ "${1:-}" == "--dry-run" ]]; then
  echo "DRY-RUN: Would restore:"
  echo "  $BACKUP_DIR/strata-ud-iq3_xxs.json.baseline -> $PROFILE_DST"
  echo "  $BACKUP_DIR/strata-llm.service.baseline -> $SERVICE_DST"
  sha256sum "$BACKUP_DIR/strata-ud-iq3_xxs.json.baseline" "$BACKUP_DIR/strata-llm.service.baseline"
  exit 0
fi

cp -a "$BACKUP_DIR/strata-ud-iq3_xxs.json.baseline" "$PROFILE_DST"
cp -a "$BACKUP_DIR/strata-llm.service.baseline" "$SERVICE_DST"

systemctl --user daemon-reload
systemctl --user restart strata-llm.service

echo "Waiting for strata /health loaded=true..."
for i in $(seq 1 90); do
  if curl -sf http://127.0.0.1:18080/health | grep -q '"loaded": true'; then
    echo "Rollback complete and service is healthy."
    exit 0
  fi
  sleep 1
done

echo "ERROR: strata did not become healthy within 90s after rollback" >&2
exit 1
