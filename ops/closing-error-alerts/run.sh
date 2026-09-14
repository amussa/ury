#!/usr/bin/env bash
set -euo pipefail
cd /opt/gelatiamo-erpnext/deploy
docker compose exec -T backend ./env/bin/python - \
    --site gelati.app.co.mz --recipient akilmussa@gmail.com "$@" \
    < /opt/gelatiamo-erpnext/ops/closing-error-alerts/monitor.py
