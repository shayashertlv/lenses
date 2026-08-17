#!/usr/bin/env python3
"""Static file server for the AR try-on prototype.

Python's stdlib server is enough here, but two things need correcting before a
browser will run this: it does not know the `.wasm` MIME type on every platform,
and serving WebAssembly with the wrong type makes the streaming compiler refuse
the file outright.

Camera access additionally requires a secure context, so bind to localhost — which
browsers treat as secure — rather than a LAN address.

    python ar/serve.py [port]
"""

from __future__ import annotations

import http.server
import socketserver
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_PORT = 8765


class Handler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {
        **http.server.SimpleHTTPRequestHandler.extensions_map,
        ".js": "text/javascript",
        ".mjs": "text/javascript",
        ".wasm": "application/wasm",
        ".glb": "model/gltf-binary",
        ".gltf": "model/gltf+json",
        ".obj": "text/plain",
        # The landmarker bundle has no registered type; anything but HTML will do.
        ".task": "application/octet-stream",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self):
        # `no-cache` rather than `no-store`, and the difference is ~40 MB per reload.
        # Both guarantee the browser revalidates before using a cached copy, so a
        # changed file is always picked up — but `no-store` forbids keeping the copy
        # at all, which made every page load re-download the wasm runtime, the
        # landmarker model and a 24 MB glasses scan just to be told they had not
        # changed. `no-cache` lets the stdlib server answer If-Modified-Since with a
        # 304 instead, which is the freshness a development prototype actually needs.
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, fmt, *args):
        # One line per request, without the date noise.
        sys.stderr.write("%s\n" % (fmt % args))

    # The one write path: re-pinning a harness baseline. The ratchet protocol
    # re-pins diag/telemetry baselines at every stage, and the pin is produced
    # INSIDE the page (the replay's own `current` object) — a static server
    # forced it through copy-paste, which is where transcription errors live.
    # Restricted to the two baseline files by exact name, localhost-bound like
    # everything else here; anything else is 403.
    _PIN_TARGETS = {
        "/tests/diag-baseline.json",
        "/tests/telemetry-baseline.json",
    }

    def do_PUT(self):  # noqa: N802 - stdlib naming
        if self.path not in self._PIN_TARGETS:
            self.send_error(403, "only baseline pins may be written")
            return
        length = int(self.headers.get("Content-Length", 0))
        if not 0 < length < 32 * 1024 * 1024:
            self.send_error(400, "bad length")
            return
        body = self.rfile.read(length)
        target = ROOT / self.path.lstrip("/")
        target.write_bytes(body)
        self.send_response(204)
        self.end_headers()


class Server(socketserver.ThreadingTCPServer):
    # Vendored wasm is ~12 MB and the browser opens several connections at once;
    # a threading server keeps one slow transfer from blocking the rest.
    daemon_threads = True

    # SO_REUSEADDR does not mean the same thing on both platforms. On POSIX it
    # permits reuse of a port still in TIME_WAIT, which is what it is wanted for.
    # On Windows it permits binding a port another process is actively listening on,
    # so a second `serve.py` starts silently beside the first, connections are split
    # between them arbitrarily, and Ctrl-C appears not to stop the server. Windows
    # also rebinds after TIME_WAIT without it, so there it buys nothing and costs
    # the EADDRINUSE that should have been raised.
    allow_reuse_address = sys.platform != "win32"


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    with Server(("127.0.0.1", port), Handler) as httpd:
        print(f"AR try-on  ->  http://127.0.0.1:{port}/")
        print("Ctrl-C to stop.")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
