#!/usr/bin/env python3
"""
Static server for the v2 app.

Two jobs beyond serving files:

1. **Shared assets during the migration.** `vendor/` (three.js, the MediaPipe
   runtime) and `assets/` (the face template, the landmark model, the sample
   portraits) live in `../ar/` and are served from there rather than copied.
   They are ~100 MB and byte-identical; two copies of a template mesh is two
   things that can drift. When `ar/` is retired, they move here and the
   `SHARED_ROOTS` table below is the only thing that changes.

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
SIBLING = HERE.parent / "ar"

# Paths served from the v1 tree, during the migration.
SHARED_ROOTS = ("/vendor/", "/assets/")

mimetypes.add_type("application/javascript", ".mjs")
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("application/wasm", ".wasm")
mimetypes.add_type("model/gltf-binary", ".glb")
mimetypes.add_type("application/octet-stream", ".task")


class Handler(SimpleHTTPRequestHandler):
    def translate_path(self, path: str) -> str:
        clean = path.split("?", 1)[0].split("#", 1)[0]
        for root in SHARED_ROOTS:
            if clean.startswith(root):
                local = HERE / clean.lstrip("/")
                if local.exists():
                    return str(local)
                shared = SIBLING / clean.lstrip("/")
                if shared.exists():
                    return str(shared)
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
        r for r in SHARED_ROOTS
        if not (HERE / r.strip("/")).exists() and not (SIBLING / r.strip("/")).exists()
    ]
    if missing:
        print(
            f"warning: {', '.join(missing)} found in neither {HERE} nor {SIBLING}.\n"
            "         The app needs the vendored three.js/MediaPipe and the face template.",
            file=sys.stderr,
        )

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"lenses ar v2  ->  http://{args.host}:{args.port}/")
    print(f"  app       {HERE}")
    print(f"  shared    {SIBLING} (vendor/, assets/)")
    print("  ctrl-c to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
