#!/usr/bin/env python3
"""Local static server for the AR app.

It serves only this project directory, gives browser assets their required MIME
types, disables stale build caching, and enables cross-origin isolation for the
MediaPipe runtime.
"""

import argparse
import mimetypes
import os
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent

mimetypes.add_type("application/javascript", ".mjs")
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("application/wasm", ".wasm")
mimetypes.add_type("model/gltf-binary", ".glb")
mimetypes.add_type("application/octet-stream", ".task")


class Handler(SimpleHTTPRequestHandler):
    def translate_path(self, path: str) -> str:
        return super().translate_path(path)

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Embedder-Policy", "require-corp")
        self.send_header("Cross-Origin-Resource-Policy", "cross-origin")
        super().end_headers()

    def log_message(self, fmt: str, *args) -> None:
        # One line per request, without the date noise.
        sys.stderr.write("  %s\n" % (fmt % args))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT") or 8020))
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    os.chdir(HERE)

    if not (HERE / "dist").exists():
        print("dist/ is missing — run `npm run build` first.", file=sys.stderr)

    missing = [
        r for r in ("vendor", "assets")
        if not (HERE / r).exists()
    ]
    if missing:
        sys.stderr.write(
            f"warning: {', '.join(missing)}/ not found under {HERE}.\n"
            "         run: node scripts/fetch-vendor.mjs\n"
        )
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"  serving {HERE} at http://{args.host}:{args.port}/")
    print("  ctrl-c to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
