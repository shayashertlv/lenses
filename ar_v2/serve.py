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
    # `PORT` before the 8020 default, so a harness that assigns a port can hand
    # one over without the command line changing. An explicit `--port` still
    # wins over both.
    #
    # Nothing here needs a FIXED port — this is a static page with no OAuth
    # callback, no webhook and no cross-origin allow-list keyed to an origin. So
    # when 8020 is taken the honest response is to take another one, not to
    # evict whatever is already there. On this machine that is routinely a
    # previous session's own server: one had been serving this tree since
    # 2026-08-23.
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
    # The server itself, and it went missing for four commits.
    #
    # `faece72` (2026-08-20) constructed it correctly. `ec9c315` (2026-08-25) —
    # "v2 owns the assets and the runtime" — rewrote this function to delete the
    # `SHARED_ROOTS` mapping into the sibling checkout, and took the
    # `ThreadingHTTPServer(...)` line with it. `main` then went straight from the
    # warnings to `server.serve_forever()` on a name that no longer existed, so
    # every attempt to start the tree raised `NameError: name 'server' is not
    # defined`.
    #
    # **Nothing in the harness starts this server, which is why nothing caught
    # it.** `check-selfcontained.mjs` reasons about the PATHS this file would
    # map — it reads the source and checks that each one resolves under this
    # directory — and never runs it. So the commit whose entire purpose was
    # making the tree servable on its own was also the commit that stopped it
    # serving, and the gate written to protect that property was satisfied
    # throughout. A path check is not a smoke test.
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
