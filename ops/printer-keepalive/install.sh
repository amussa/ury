#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

install -o root -g root -m 0755 \
	"$SOURCE_DIR/printer_keepalive.py" \
	/usr/local/libexec/gelatiamo-printer-keepalive
install -o root -g root -m 0644 \
	"$SOURCE_DIR/gelatiamo-printer-keepalive.service" \
	/etc/systemd/system/gelatiamo-printer-keepalive.service
install -o root -g root -m 0644 \
	"$SOURCE_DIR/gelatiamo-printer-keepalive.timer" \
	/etc/systemd/system/gelatiamo-printer-keepalive.timer

systemctl daemon-reload
systemctl enable --now gelatiamo-printer-keepalive.timer
systemctl start gelatiamo-printer-keepalive.service

systemctl status gelatiamo-printer-keepalive.timer --no-pager
journalctl -u gelatiamo-printer-keepalive.service -n 20 --no-pager
