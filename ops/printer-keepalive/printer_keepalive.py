#!/usr/bin/env python3
"""Keep Cloudflare private routes to the Polana printers warm.

The probe only completes a TCP handshake and closes the socket. It never sends
printer data, so it cannot produce paper or alter a print job.
"""

from __future__ import annotations

import os
import socket
import sys
import time

DEFAULT_TARGETS = "192.168.18.2:9100,192.168.18.3:9100"
CONNECT_TIMEOUT_SECONDS = 5.0
RETRY_DELAY_SECONDS = 1.0
ATTEMPTS = 2


def parse_targets(value: str) -> list[tuple[str, int]]:
	targets = []
	for entry in value.split(","):
		host, separator, port = entry.strip().rpartition(":")
		if not separator or not host or not port.isdigit():
			raise ValueError(f"invalid printer target: {entry!r}")
		targets.append((host, int(port)))
	return targets


def probe(host: str, port: int) -> bool:
	for attempt in range(1, ATTEMPTS + 1):
		started = time.monotonic()
		try:
			connection = socket.create_connection((host, port), timeout=CONNECT_TIMEOUT_SECONDS)
			local_address = connection.getsockname()[0]
			connection.close()
			elapsed_ms = round((time.monotonic() - started) * 1000)
			print(
				f"printer_keepalive target={host}:{port} outcome=connected "
				f"attempt={attempt} elapsed_ms={elapsed_ms} local={local_address}",
				flush=True,
			)
			return True
		except OSError as exc:
			elapsed_ms = round((time.monotonic() - started) * 1000)
			print(
				f"printer_keepalive target={host}:{port} outcome=failed "
				f"attempt={attempt} elapsed_ms={elapsed_ms} "
				f"error={type(exc).__name__}:{exc}",
				flush=True,
			)
			if attempt < ATTEMPTS:
				time.sleep(RETRY_DELAY_SECONDS)
	return False


def main() -> int:
	try:
		targets = parse_targets(os.environ.get("PRINTER_TARGETS", DEFAULT_TARGETS))
	except ValueError as exc:
		print(f"printer_keepalive configuration_error={exc}", flush=True)
		return 2

	failed = [f"{host}:{port}" for host, port in targets if not probe(host, port)]
	if failed:
		print(f"printer_keepalive summary=unreachable targets={','.join(failed)}", flush=True)
		return 1

	print(f"printer_keepalive summary=healthy targets={len(targets)}", flush=True)
	return 0


if __name__ == "__main__":
	sys.exit(main())
