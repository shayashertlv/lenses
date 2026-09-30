"""Serve only explicitly selected GLB snapshots for local AR comparison."""
from __future__ import annotations

import argparse
import hashlib
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode

from bsa.archeck import front_width_mm


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--glb", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--port", type=int, default=8796)
    parser.add_argument("--ar-app", default="http://127.0.0.1:8240/")
    args = parser.parse_args()
    assets, links = {}, []
    for label, path, route in (("Astra candidate", args.glb, "/candidate.glb"),
                               ("Unchanged native baseline", args.baseline, "/baseline.glb")):
        if path is None:
            continue
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        assets[route] = data
        url = args.ar_app + "?" + urlencode({
            "model": f"http://127.0.0.1:{args.port}{route}", "name": label,
            "width": round(front_width_mm(path), 2), "clip": -.14, "sha256": digest,
        })
        links.append(f'<li><a href="{html.escape(url, quote=True)}">{label}</a>'
                     f'<p><code>{digest}</code></p></li>')
        print(f"{label}: {url}", flush=True)
    page = ("<!doctype html><meta charset=utf-8><title>Astra Blender AR comparison</title>"
            "<style>body{font:18px system-ui;max-width:960px;margin:50px auto;padding:20px}"
            "li{margin:30px 0}code{font-size:12px;overflow-wrap:anywhere}</style>"
            "<h1>Astra Blender review</h1><p>The links use exact exported GLB snapshots in the local AR app. "
            "Open the camera in that app to try them on. Compatibility is not a quality verdict.</p><ul>"
            + "".join(links) + "</ul>").encode()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            route = self.path.split("?", 1)[0]
            data = page if route == "/" else assets.get(route)
            if data is None:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8" if route == "/" else "model/gltf-binary")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cross-Origin-Resource-Policy", "cross-origin")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    print(f"Comparison: http://127.0.0.1:{args.port}/", flush=True)
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
