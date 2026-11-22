"""Command-line entry point for the VirtUI Manager web dashboard.

Starts a local FastAPI/uvicorn server exposing the read-only API and the
static dashboard. Binds to 127.0.0.1 by default; a random bearer token is
generated per run and printed to the console.
"""

from __future__ import annotations

import argparse
import logging
import secrets
import sys
import threading

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8900


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="virtui-web",
        description="Serve the VirtUI Manager web dashboard (read-only).",
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"Bind address (default: {DEFAULT_HOST})")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"TCP port (default: {DEFAULT_PORT})")
    parser.add_argument(
        "--token",
        default=None,
        help="API token (default: generated randomly and printed once)",
    )
    parser.add_argument(
        "--stats-interval",
        type=int,
        default=None,
        help="Live stats push interval in seconds (default: STATS_INTERVAL from config)",
    )
    parser.add_argument("--log-level", default="warning", help="uvicorn log level (default: warning)")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        import uvicorn  # pylint: disable=import-outside-toplevel
    except ImportError:
        print('Missing dependencies. Install them with: pip install "virtui-manager[web]"')
        return 1

    from .api import Backend, create_app  # pylint: disable=import-outside-toplevel

    token = args.token or secrets.token_urlsafe(24)
    backend = Backend()
    app = create_app(backend, token, stats_interval=args.stats_interval)

    # Connect in the background: unreachable servers (SSH retries) must not
    # delay startup. The API reports them as disconnected until ready.
    threading.Thread(target=backend.connect_configured_servers, daemon=True).start()

    print(f"VirtUI web dashboard: http://{args.host}:{args.port}/#token={token}", flush=True)
    if args.host in ("0.0.0.0", "::"):
        print("WARNING: listening on all interfaces without TLS.", flush=True)
        print("         Prefer an SSH tunnel: ssh -L 8900:127.0.0.1:8900 <host>", flush=True)
    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
