#!/usr/bin/env bash
# Copy this directory to the production host before running this installer.
set -euo pipefail
ALERT_SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ALERT_INSTALL_DIR=/opt/gelatiamo-erpnext/ops/closing-error-alerts
test "$(id -u)" = 0
install -d -m 0755 "$ALERT_INSTALL_DIR"
if [ "$ALERT_SOURCE_DIR" != "$ALERT_INSTALL_DIR" ]; then
    install -m 0644 "$ALERT_SOURCE_DIR/monitor.py" "$ALERT_INSTALL_DIR/monitor.py"
    install -m 0755 "$ALERT_SOURCE_DIR/run.sh" "$ALERT_INSTALL_DIR/run.sh"
fi
bash "$ALERT_INSTALL_DIR/run.sh" --dry-run
install -m 0644 "$ALERT_SOURCE_DIR/gelatiamo-closing-error-alerts.service" /etc/systemd/system/
install -m 0644 "$ALERT_SOURCE_DIR/gelatiamo-closing-error-alerts.timer" /etc/systemd/system/
systemd-analyze verify /etc/systemd/system/gelatiamo-closing-error-alerts.service /etc/systemd/system/gelatiamo-closing-error-alerts.timer
systemctl daemon-reload
systemctl enable --now gelatiamo-closing-error-alerts.timer
systemctl start gelatiamo-closing-error-alerts.service
systemctl list-timers gelatiamo-closing-error-alerts.timer --no-pager
