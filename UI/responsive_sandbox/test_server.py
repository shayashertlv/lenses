"""Minimal test server to preview templates without backend dependencies."""
import sys
import os
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(__file__))
from templates import LANDING_HTML, FREE_SEARCH_HTML, LENS_RECOLOR_HTML, STOREFRONT_HTML

PAGES = {
    "landing":      LANDING_HTML,
    "free-search":  FREE_SEARCH_HTML,
    "lens-recolor": LENS_RECOLOR_HTML,
    "storefront":   STOREFRONT_HTML,
}

_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

_MIME = {
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
}


class PreviewHandler(BaseHTTPRequestHandler):
    default_page = "landing"   # overridden per-instance via class var

    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        page = qs.get("p", [self.default_page])[0]

        if parsed.path.startswith("/static/"):
            self._serve_static(parsed.path)
        elif parsed.path in ("/", ""):
            html = PAGES.get(page, LANDING_HTML)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html.encode())
        elif parsed.path == "/api/catalog":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"[]")
        else:
            self.send_response(404)
            self.end_headers()

    def _serve_static(self, url_path):
        rel = url_path[len("/static/"):]
        safe = os.path.normpath(rel)
        if safe.startswith("..") or os.path.isabs(safe):
            self.send_response(403)
            self.end_headers()
            return
        file_path = _STATIC_DIR / safe
        if not file_path.is_file():
            self.send_response(404)
            self.end_headers()
            return
        ext = file_path.suffix.lower()
        mime = _MIME.get(ext, "application/octet-stream")
        data = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8099
    page = sys.argv[2] if len(sys.argv) > 2 else "landing"
    PreviewHandler.default_page = page
    server = HTTPServer(("0.0.0.0", port), PreviewHandler)
    print(f"Preview server at http://localhost:{port}  (page: {page})")
    server.serve_forever()
