"""
Lenses UI — Two modes:

1. Smart Fit:   Upload portrait -> face analysis -> inventory match -> virtual try-on.
2. Free Search: Upload portrait + choose preferences -> semantic search -> virtual try-on.

Frontend polls /api/status/<id> and progressively reveals results as each
try-on finishes.
"""

import os
from http.server import HTTPServer
from socketserver import ThreadingMixIn

from UI.handler import Handler


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def main():
    host = "0.0.0.0"
    port = int(os.environ.get("PORT", 8080))
    server = ThreadedHTTPServer((host, port), Handler)
    print(f"Lenses UI running at http://{host}:{port}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.server_close()


if __name__ == "__main__":
    main()
