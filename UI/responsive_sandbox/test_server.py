"""Fixture server: preview every page in every state without touching Gemini.

The interesting UI (loading stages, results, error cards) is hidden behind
style.display and only appears after a real 20-50s generation. This server
serves the real templates, the real catalogue and canned pipeline responses so
every state is a deterministic, zero-cost URL.

    python -m UI.responsive_sandbox.test_server 8099 storefront

    /?p=storefront              a page
    /?p=landing&stage=done      pin every job to a stage
    /api/status/<sid>           canned; advances through the real stage
                                sequence on successive polls unless pinned
"""
import base64
import json
import os
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from UI.templates import TEMPLATE_FILES, get_template
from UI.pipelines import get_catalog_products

_ROOT = Path(__file__).resolve().parents[2]
_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
_CATALOG_IMAGES = _ROOT / "lenses" / "catalog" / "images"

_MIME = {
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".woff2": "font/woff2",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}

# Real stage sequences, and how many 2s polls each holds for. Compressed so a
# full run is walkable in a few seconds instead of thirty.
_TRYON_STAGES = [("uploading", 1), ("analyzing", 2), ("matching", 1),
                 ("tryon", 3), ("primary_ready", 2), ("done", 999)]
_RECOLOR_STAGES = [("uploading", 1), ("recoloring", 3), ("primary_ready", 2), ("done", 999)]

_LENS_COLORS = ["Soft Black", "Smoke Gray", "Ocean Blue"]

_FACE_SUMMARY = {
    "face_shape": "Oval",
    "face_shape_description": "Longer than wide, widest at the cheekbone.",
    "key_geometry": "Tapered jawline",
    "key_geometry_description": "Narrows steadily from cheekbone to chin.",
    "color_profile": "High contrast",
    "color_profile_description": "Dark hair against a warm mid skin tone.",
    "hair_color_hex": "#2B2118",
    "eye_color_hex": "#4A5D3F",
    "skin_tone_hex": "#D9B08C",
}

_FACE_INSIGHTS = (
    "Your face runs a little longer than it is wide, and the widest point sits at the cheekbone "
    "rather than the brow. That is the oval, and it is the one shape opticians quietly envy, "
    "because almost every frame width sits well on it.\n\n"
    "The jawline is what is actually driving this pick. It tapers, so a frame carrying a strong "
    "horizontal top line gives the face something to sit beneath and holds the width up where the "
    "bone already is.\n\n"
    "Your colouring is high contrast, which means a frame can afford to be definite. A pale, thin "
    "wire would read as hesitant next to this much tonal separation."
)

_polls: dict[str, int] = {}
_img_cache: dict[str, str] = {}


def _fixture_b64(index: int = 0) -> str:
    """A real catalogue photo, stood in for a generated image."""
    key = str(index)
    if key not in _img_cache:
        files = sorted(p for p in _CATALOG_IMAGES.glob("*.jpg"))
        if not files:
            return ""
        path = files[index % len(files)]
        _img_cache[key] = base64.b64encode(path.read_bytes()).decode()
    return _img_cache[key]


def _resolve_stage(sid: str, pinned: str | None, stages) -> str:
    if pinned:
        return pinned
    n = _polls.get(sid, 0)
    _polls[sid] = n + 1
    seen = 0
    for name, hold in stages:
        seen += hold
        if n < seen:
            return name
    return stages[-1][0]


def _options(count: int, stage: str) -> dict:
    products = get_catalog_products()[:count]
    out: dict = {}
    for i, p in enumerate(products):
        ready = stage == "done" or (stage == "primary_ready" and i == 0)
        out[f"opt{i}"] = {
            "name": p["name"], "brand": p["brand"], "model": p.get("model", ""),
            "price": p["price"], "currency": p["currency"],
            "score": 92 - i * 7, "fit_score": 95 - i * 5,
            "style_score": 88 - i * 6, "color_score": 91 - i * 4,
            "shape": p.get("shape", ""), "material": p.get("material", ""),
            "color": p.get("color", ""), "product_id": p["id"],
            "product_b64": _fixture_b64(i),
            "tryon_status": "done" if ready else "pending",
            "tryon_b64": _fixture_b64(i + 3) if ready else "",
            "tryon_error": "",
        }
    return out


class PreviewHandler(BaseHTTPRequestHandler):
    default_page = "landing"

    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        path = parsed.path
        pinned = qs.get("stage", [None])[0]

        if path.startswith("/static/"):
            return self._serve_static(path)
        if path in ("/", ""):
            page = qs.get("p", [self.default_page])[0]
            if page not in TEMPLATE_FILES:
                page = self.default_page
            return self._send(200, "text/html; charset=utf-8", get_template(page).encode())
        if path == "/api/catalog":
            return self._json(get_catalog_products())
        if path.startswith("/api/catalog-image/"):
            return self._serve_catalog_image(path[len("/api/catalog-image/"):])
        if path.startswith("/api/status/"):
            return self._json(self._status(path[len("/api/status/"):], pinned))
        if path.startswith("/api/recolor-status/"):
            return self._json(self._recolor(path[len("/api/recolor-status/"):], pinned))
        if path.startswith("/api/storefront-recolor-status/"):
            sid = path[len("/api/storefront-recolor-status/"):]
            stage = _resolve_stage(sid, pinned, _RECOLOR_STAGES)
            return self._json({"status": "done" if stage == "done" else "processing",
                               "stage": stage, "error": "",
                               "result_b64": _fixture_b64(5) if stage in ("primary_ready", "done") else ""})
        self._send(404, "text/plain", b"not found")

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        sid = f"fx{len(_polls)}-{os.urandom(3).hex()}"
        self._json({"session_id": sid})

    def _status(self, sid: str, pinned: str | None) -> dict:
        stage = _resolve_stage(sid, pinned, _TRYON_STAGES)
        if stage == "error":
            return {"status": "error", "stage": "error", "error": "Fixture error state.",
                    "num_options": 0}
        body = {
            "status": "done" if stage == "done" else "processing",
            "stage": stage, "error": "",
            "num_options": 3 if stage in ("matching", "tryon", "primary_ready", "done") else 0,
            "portrait_b64": _fixture_b64(9) if stage != "uploading" else "",
        }
        if stage in ("matching", "tryon", "primary_ready", "done"):
            body["face_insights"] = _FACE_INSIGHTS
            body["face_summary"] = _FACE_SUMMARY
            body.update(_options(3, stage))
        return body

    def _recolor(self, sid: str, pinned: str | None) -> dict:
        stage = _resolve_stage(sid, pinned, _RECOLOR_STAGES)
        body = {"status": "done" if stage == "done" else "processing", "stage": stage,
                "error": "", "num_colors": 3, "portrait_b64": _fixture_b64(9)}
        for i, name in enumerate(_LENS_COLORS):
            ready = stage == "done" or (stage == "primary_ready" and i == 0)
            body[f"color{i}"] = {"name": name, "status": "done" if ready else "pending",
                                 "b64": _fixture_b64(i + 6) if ready else "", "error": ""}
        return body

    def _serve_static(self, url_path):
        rel = url_path[len("/static/"):]
        safe = os.path.normpath(rel)
        if safe.startswith("..") or os.path.isabs(safe):
            return self._send(403, "text/plain", b"forbidden")
        file_path = _STATIC_DIR / safe
        if not file_path.is_file():
            return self._send(404, "text/plain", b"not found")
        self._send(200, _MIME.get(file_path.suffix.lower(), "application/octet-stream"),
                   file_path.read_bytes())

    def _serve_catalog_image(self, fname):
        path = _CATALOG_IMAGES / os.path.basename(fname)
        if not path.is_file():
            return self._send(404, "text/plain", b"not found")
        self._send(200, _MIME.get(path.suffix.lower(), "application/octet-stream"),
                   path.read_bytes())

    def _json(self, payload):
        self._send(200, "application/json", json.dumps(payload).encode())

    def _send(self, code, mime, data):
        self.send_response(code)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        pass


class ThreadingPreviewServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8099
    page = sys.argv[2] if len(sys.argv) > 2 else "landing"
    PreviewHandler.default_page = page
    print(f"Fixture server at http://localhost:{port}/?p={page}")
    print(f"  stages: {', '.join(s for s, _ in _TRYON_STAGES)}")
    ThreadingPreviewServer(("0.0.0.0", port), PreviewHandler).serve_forever()
