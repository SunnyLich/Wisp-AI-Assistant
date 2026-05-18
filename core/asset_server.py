"""
core/asset_server.py — Tiny localhost HTTP server for the assets/ folder.

Starts a thread-based HTTP server that serves the assets directory so that
PyQtWebEngine can load VRM files and other resources without the fetch()
restrictions that apply to file:// URLs.
"""
from __future__ import annotations
import socket
import threading
from http.server import SimpleHTTPRequestHandler, HTTPServer


def _find_free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _SilentHandler(SimpleHTTPRequestHandler):
    """Like SimpleHTTPRequestHandler but without request logging noise."""

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        pass  # suppress stdout noise


def start(assets_dir: str) -> int:
    """
    Serve *assets_dir* on a random localhost port in a daemon thread.
    Returns the port number.
    """
    port = _find_free_port()

    def _run() -> None:
        import functools
        handler = functools.partial(_SilentHandler, directory=assets_dir)
        server = HTTPServer(("127.0.0.1", port), handler)
        server.serve_forever()

    t = threading.Thread(target=_run, daemon=True, name="asset-server")
    t.start()
    return port
