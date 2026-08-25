#!/usr/bin/env python3
"""
Static server for the v2 app.

Two jobs beyond serving files:

1. **Everything is served from this directory.** `assets/` (the face template,
   the eleven eyewear models, the landmark model, the sample portraits) is
   tracked here; `vendor/` (three.js, the MediaPipe runtime) is fetched here
   and SHA-256 verified by `scripts/fetch-vendor.mjs`. Both used to be reached
   across into the sibling v1 checkout, which meant this tree could only run on a
   machine that also had the v1 checkout — and nothing said so.
   `scripts/check-selfcontained.mjs` now fails the build if that comes back.

2. **Correct MIME types and no caching.** A `.mjs` served as `text/plain` fails
   with a module error that names the wrong problem, and a cached `dist/` after
   a rebuild is a debugging session that finds nothing.

Cross-origin isolation headers are set because they cost nothing here and are
what `SharedArrayBuffer` needs — which the MediaPipe WASM runtime uses for its
threaded build.
"""

import argparse
import mimetypes
import os
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent

# **Nothing is served from anywhere but this directory.**
#
# This used to reach into ../ar for /vendor/ and /assets/, which made the tree
# unservable anywhere the v1 checkout did not sit beside it. Both now live
# here: `assets/` is tracked, `vendor/` is fetched and SHA-256 verified by
# `scripts/fetch-vendor.mjs`. `scripts/check-selfcontained.mjs` fails the build
# if a path into the sibling comes back.

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
    parser.add_argument("--port", type=int, default=8020)
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
    print("  ctrl-c to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
