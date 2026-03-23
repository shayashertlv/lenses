"""
Lenses UI — Two modes:

1. Smart Fit:   Upload portrait -> face analysis -> inventory match -> virtual try-on.
2. Free Search: Upload portrait + choose preferences -> semantic search -> virtual try-on.

Frontend polls /api/status/<id> and progressively reveals results as each
try-on finishes.
"""

from http.server import HTTPServer

from UI.handler import Handler


def main():
    host, port = "127.0.0.1", 8080
    server = HTTPServer((host, port), Handler)
    print(f"Lenses UI running at http://{host}:{port}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.server_close()


if __name__ == "__main__":
    main()
