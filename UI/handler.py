"""
HTTP request handler and multipart form-data parsers.
"""

import base64
import json
import os
import threading
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler
from pathlib import Path

from UI.config import sessions
from UI.config import CATALOG_IMAGES_DIR
from UI.pipelines import (
    run_pipeline, run_free_search_pipeline, run_recolor_pipeline,
    run_storefront_tryon_pipeline, run_single_recolor_pipeline,
    run_storefront_smartfit_pipeline, run_storefront_freesearch_pipeline,
    get_catalog_products,
)
from UI.templates import get_template

_STATIC_DIR = Path(__file__).parent / "static"

_STATIC_MIME = {
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".woff2": "font/woff2",
}


class Handler(BaseHTTPRequestHandler):

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path

        if path == "/":
            self._html(get_template("landing"))
        elif path == "/free-search":
            self._html(get_template("free-search"))
        elif path == "/lens-recolor":
            self._html(get_template("lens-recolor"))
        elif path == "/storefront":
            self._html(get_template("storefront"))
        elif path.startswith("/static/"):
            self._serve_static(path)
        elif path == "/api/catalog":
            self._json(200, get_catalog_products())
        elif path.startswith("/api/catalog-image/"):
            fname = path[len("/api/catalog-image/"):]
            self._serve_catalog_image(fname)
        elif path.startswith("/api/status/"):
            sid = path[len("/api/status/"):]
            self._serve_status(sid)
        elif path.startswith("/api/recolor-status/"):
            sid = path[len("/api/recolor-status/"):]
            self._serve_recolor_status(sid)
        elif path.startswith("/api/storefront-recolor-status/"):
            sid = path[len("/api/storefront-recolor-status/"):]
            self._serve_storefront_recolor_status(sid)
        else:
            self.send_error(404)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/upload":
            self._handle_upload()
        elif path == "/api/free-search":
            self._handle_free_search()
        elif path == "/api/lens-recolor":
            self._handle_lens_recolor()
        elif path == "/api/storefront-tryon":
            self._handle_storefront_tryon()
        elif path == "/api/storefront-recolor":
            self._handle_storefront_recolor()
        elif path == "/api/storefront-smartfit":
            self._handle_storefront_smartfit()
        elif path == "/api/storefront-freesearch":
            self._handle_storefront_freesearch()
        else:
            self.send_error(404)

    # ── Smart Fit upload ──────────────────────────────────────────────
    def _handle_upload(self):
        ctype = self.headers.get("Content-Type", "")
        clen = int(self.headers.get("Content-Length", 0))

        if "multipart/form-data" not in ctype:
            return self._json(400, {"error": "Expected multipart/form-data"})

        boundary = ctype.split("boundary=")[1].strip()
        body = self.rfile.read(clen)
        file_data, file_name = _parse_multipart_file(body, boundary)

        if not file_data:
            return self._json(400, {"error": "No file in upload"})

        sid = uuid.uuid4().hex[:12]
        sessions[sid] = {"status": "processing", "stage": "uploading",
                         "error": None, "num_options": 0}

        threading.Thread(
            target=run_pipeline, args=(sid, file_data, file_name), daemon=True
        ).start()

        self._json(200, {"session_id": sid})

    # ── Free Search upload ────────────────────────────────────────────
    def _handle_free_search(self):
        ctype = self.headers.get("Content-Type", "")
        clen = int(self.headers.get("Content-Length", 0))

        if "multipart/form-data" not in ctype:
            return self._json(400, {"error": "Expected multipart/form-data"})

        boundary = ctype.split("boundary=")[1].strip()
        body = self.rfile.read(clen)
        fields = _parse_multipart_all(body, boundary)

        file_data = fields.get("_file_data")
        file_name = fields.get("_file_name", "upload.jpg")

        if not file_data:
            return self._json(400, {"error": "No photo uploaded"})

        # Extract preferences from form fields
        preferences = {}
        for key in ("frame_shape", "frame_color", "lens_color", "frame_material",
                    "frame_thickness", "rim_type", "lens_type", "lens_size",
                    "aesthetic", "gender", "occasion", "max_price"):
            val = fields.get(key, "")
            if val:
                preferences[key] = val

        sid = uuid.uuid4().hex[:12]
        sessions[sid] = {"status": "processing", "stage": "uploading",
                         "error": None, "num_options": 0}

        threading.Thread(
            target=run_free_search_pipeline,
            args=(sid, file_data, file_name, preferences),
            daemon=True,
        ).start()

        self._json(200, {"session_id": sid})

    # ── Lens Recolor upload ──────────────────────────────────────────
    def _handle_lens_recolor(self):
        ctype = self.headers.get("Content-Type", "")
        clen = int(self.headers.get("Content-Length", 0))

        if "multipart/form-data" not in ctype:
            return self._json(400, {"error": "Expected multipart/form-data"})

        boundary = ctype.split("boundary=")[1].strip()
        body = self.rfile.read(clen)
        fields = _parse_multipart_all(body, boundary)

        file_data = fields.get("_file_data")
        file_name = fields.get("_file_name", "upload.jpg")

        if not file_data:
            return self._json(400, {"error": "No photo uploaded"})

        colors = []
        for key in ("color1", "color2", "color3"):
            val = fields.get(key, "").strip()
            if val:
                colors.append(val)

        if len(colors) < 3:
            return self._json(400, {"error": "Please select exactly 3 lens colors"})

        sid = uuid.uuid4().hex[:12]
        sessions[sid] = {"status": "processing", "stage": "uploading",
                         "error": None, "num_colors": 0}

        threading.Thread(
            target=run_recolor_pipeline,
            args=(sid, file_data, file_name, colors),
            daemon=True,
        ).start()

        self._json(200, {"session_id": sid})

    # ── Storefront try-on ───────────────────────────────────────────────
    def _handle_storefront_tryon(self):
        ctype = self.headers.get("Content-Type", "")
        clen = int(self.headers.get("Content-Length", 0))

        if "multipart/form-data" not in ctype:
            return self._json(400, {"error": "Expected multipart/form-data"})

        boundary = ctype.split("boundary=")[1].strip()
        body = self.rfile.read(clen)
        fields = _parse_multipart_all(body, boundary)

        file_data = fields.get("_file_data")
        file_name = fields.get("_file_name", "upload.jpg")
        product_id = fields.get("product_id", "").strip()

        if not file_data:
            return self._json(400, {"error": "No photo uploaded"})
        if not product_id:
            return self._json(400, {"error": "No product_id provided"})

        sid = uuid.uuid4().hex[:12]
        sessions[sid] = {"status": "processing", "stage": "uploading",
                         "error": None, "num_options": 0}

        threading.Thread(
            target=run_storefront_tryon_pipeline,
            args=(sid, file_data, file_name, product_id),
            daemon=True,
        ).start()

        self._json(200, {"session_id": sid})

    # ── Storefront Smart Fit (single best match) ─────────────────────
    def _handle_storefront_smartfit(self):
        ctype = self.headers.get("Content-Type", "")
        clen = int(self.headers.get("Content-Length", 0))

        if "multipart/form-data" not in ctype:
            return self._json(400, {"error": "Expected multipart/form-data"})

        boundary = ctype.split("boundary=")[1].strip()
        body = self.rfile.read(clen)
        fields = _parse_multipart_all(body, boundary)

        file_data = fields.get("_file_data")
        file_name = fields.get("_file_name", "upload.jpg")

        if not file_data:
            return self._json(400, {"error": "No photo uploaded"})

        sid = uuid.uuid4().hex[:12]
        sessions[sid] = {"status": "processing", "stage": "uploading",
                         "error": None, "num_options": 0}

        threading.Thread(
            target=run_storefront_smartfit_pipeline,
            args=(sid, file_data, file_name),
            daemon=True,
        ).start()

        self._json(200, {"session_id": sid})

    # ── Storefront Free Search (single best match) ───────────────────
    def _handle_storefront_freesearch(self):
        ctype = self.headers.get("Content-Type", "")
        clen = int(self.headers.get("Content-Length", 0))

        if "multipart/form-data" not in ctype:
            return self._json(400, {"error": "Expected multipart/form-data"})

        boundary = ctype.split("boundary=")[1].strip()
        body = self.rfile.read(clen)
        fields = _parse_multipart_all(body, boundary)

        file_data = fields.get("_file_data")
        file_name = fields.get("_file_name", "upload.jpg")

        if not file_data:
            return self._json(400, {"error": "No photo uploaded"})

        preferences = {}
        for key in ("frame_shape", "frame_color", "lens_color", "frame_material",
                    "frame_thickness", "rim_type", "lens_type", "lens_size",
                    "aesthetic", "gender", "occasion", "max_price"):
            val = fields.get(key, "")
            if val:
                preferences[key] = val

        sid = uuid.uuid4().hex[:12]
        sessions[sid] = {"status": "processing", "stage": "uploading",
                         "error": None, "num_options": 0}

        threading.Thread(
            target=run_storefront_freesearch_pipeline,
            args=(sid, file_data, file_name, preferences),
            daemon=True,
        ).start()

        self._json(200, {"session_id": sid})

    # ── Storefront single-color recolor ──────────────────────────────
    def _handle_storefront_recolor(self):
        clen = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(clen)
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return self._json(400, {"error": "Invalid JSON"})

        image_b64 = data.get("image_b64", "")
        color = data.get("color", "").strip()

        if not image_b64:
            return self._json(400, {"error": "No image provided"})
        if not color:
            return self._json(400, {"error": "No color selected"})

        try:
            image_bytes = base64.b64decode(image_b64)
        except Exception:
            return self._json(400, {"error": "Invalid base64 image"})

        sid = uuid.uuid4().hex[:12]
        sessions[sid] = {"status": "processing", "stage": "uploading",
                         "error": None, "result_b64": None}

        threading.Thread(
            target=run_single_recolor_pipeline,
            args=(sid, image_bytes, color),
            daemon=True,
        ).start()

        self._json(200, {"session_id": sid})

    def _serve_storefront_recolor_status(self, sid: str):
        sess = sessions.get(sid)
        if not sess:
            return self._json(404, {"error": "Unknown session"})
        self._json(200, {
            "status": sess["status"],
            "stage": sess.get("stage", ""),
            "error": sess.get("error"),
            "result_b64": sess.get("result_b64"),
        })

    # ── Static file serving ──────────────────────────────────────────
    def _serve_static(self, url_path: str):
        # /static/css/common.css -> static/css/common.css
        rel = url_path[len("/static/"):]
        # Prevent directory traversal
        safe = os.path.normpath(rel)
        if safe.startswith("..") or os.path.isabs(safe):
            return self.send_error(403)
        file_path = _STATIC_DIR / safe
        if not file_path.is_file():
            return self.send_error(404)

        ext = file_path.suffix.lower()
        mime = _STATIC_MIME.get(ext, "application/octet-stream")
        data = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    # ── Catalog image serving ─────────────────────────────────────────
    def _serve_catalog_image(self, filename: str):
        import os
        # Sanitize filename to prevent directory traversal
        safe_name = os.path.basename(filename)
        img_path = CATALOG_IMAGES_DIR / safe_name
        if not img_path.is_file():
            return self.send_error(404)

        ext = img_path.suffix.lower()
        mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".png": "image/png", ".webp": "image/webp"}.get(ext, "image/jpeg")

        data = img_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=86400")
        self.end_headers()
        self.wfile.write(data)

    # ── recolor status polling ─────────────────────────────────────────
    def _serve_recolor_status(self, sid: str):
        sess = sessions.get(sid)
        if not sess:
            return self._json(404, {"error": "Unknown session"})

        resp = {
            "status": sess["status"],
            "stage": sess.get("stage", ""),
            "error": sess.get("error"),
            "num_colors": sess.get("num_colors", 0),
            "portrait_b64": sess.get("portrait_b64"),
        }

        for i in range(sess.get("num_colors", 0)):
            c = sess.get(f"color{i}")
            if not c:
                continue
            resp[f"color{i}"] = {
                "name": c["name"],
                "status": c["status"],
                "b64": c["b64"],
                "error": c.get("error"),
            }

        self._json(200, resp)

    # ── status polling ────────────────────────────────────────────────
    def _serve_status(self, sid: str):
        sess = sessions.get(sid)
        if not sess:
            return self._json(404, {"error": "Unknown session"})

        resp: dict = {
            "status": sess["status"],
            "stage": sess.get("stage", ""),
            "error": sess.get("error"),
            "num_options": sess.get("num_options", 0),
            "portrait_b64": sess.get("portrait_b64"),
            "face_insights": sess.get("face_insights", []),
            "face_summary": sess.get("face_summary", {}),
        }

        for i in range(sess.get("num_options", 0)):
            opt = sess.get(f"opt{i}")
            if not opt:
                continue
            resp[f"opt{i}"] = {
                "name": opt["name"],
                "brand": opt["brand"],
                "model": opt["model"],
                "price": opt["price"],
                "currency": opt["currency"],
                "score": opt["score"],
                "fit_score": opt.get("fit_score", opt["score"]),
                "style_score": opt.get("style_score", opt["score"]),
                "color_score": opt.get("color_score", opt["score"]),
                "shape": opt["shape"],
                "material": opt["material"],
                "color": opt["color"],
                "product_b64": opt["product_b64"],
                "product_id": opt.get("product_id"),
                "tryon_status": opt["tryon_status"],
                "tryon_b64": opt["tryon_b64"],
                "tryon_error": opt.get("tryon_error"),
            }

        self._json(200, resp)

    # ── helpers ───────────────────────────────────────────────────────
    def _html(self, content: str):
        """A page, always revalidated.

        Versioning every /static/ URL only keeps a deploy visible if the
        document carrying those URLs is itself fresh. This response went out
        with no Cache-Control, no ETag and no Last-Modified, which does not mean
        "do not cache" — it means the browser is free to guess, and a heuristic
        hit serves yesterday's HTML with yesterday's asset URLs on it. The
        stamps are only as fresh as the page they ride on.

        `no-cache`, not `no-store`: the page may be stored and must be
        revalidated before use, and unlike `no-store` it leaves the back/forward
        cache alone — which this product leans on, since a phone can discard the
        page while its camera is open and restore it on the way back.
        """
        data = content.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, code: int, obj: dict):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        print(f"[{self.log_date_time_string()}] {fmt % args}")


# ══════════════════════════════════════════════════════════════════════════════
# MULTIPART PARSERS
# ══════════════════════════════════════════════════════════════════════════════

def _parse_multipart_file(body: bytes, boundary: str) -> tuple[bytes | None, str]:
    """Extract the first file from a multipart/form-data body."""
    delim = f"--{boundary}".encode()
    for part in body.split(delim):
        if b"filename=" not in part:
            continue
        hdr_end = part.find(b"\r\n\r\n")
        if hdr_end == -1:
            continue
        header = part[:hdr_end].decode("utf-8", errors="replace")
        payload = part[hdr_end + 4:]
        if payload.endswith(b"--\r\n"):
            payload = payload[:-4]
        elif payload.endswith(b"--"):
            payload = payload[:-2]
        if payload.endswith(b"\r\n"):
            payload = payload[:-2]

        fname = "upload.jpg"
        for seg in header.split("\r\n"):
            if 'filename="' in seg:
                s = seg.index('filename="') + 10
                fname = seg[s:seg.index('"', s)]
                break
        return payload, fname

    return None, ""


def _parse_multipart_all(body: bytes, boundary: str) -> dict:
    """
    Parse all fields from multipart/form-data.
    Returns dict with string fields by name.
    File data is stored as _file_data / _file_name.
    """
    fields = {}
    delim = f"--{boundary}".encode()

    for part in body.split(delim):
        if b"Content-Disposition" not in part:
            continue
        hdr_end = part.find(b"\r\n\r\n")
        if hdr_end == -1:
            continue
        header = part[:hdr_end].decode("utf-8", errors="replace")
        payload = part[hdr_end + 4:]
        if payload.endswith(b"--\r\n"):
            payload = payload[:-4]
        elif payload.endswith(b"--"):
            payload = payload[:-2]
        if payload.endswith(b"\r\n"):
            payload = payload[:-2]

        # Extract field name
        name = None
        filename = None
        for seg in header.split("\r\n"):
            if 'name="' in seg:
                s = seg.index('name="') + 6
                name = seg[s:seg.index('"', s)]
            if 'filename="' in seg:
                s = seg.index('filename="') + 10
                filename = seg[s:seg.index('"', s)]

        if not name:
            continue

        if filename is not None:
            # File field
            fields["_file_data"] = payload
            fields["_file_name"] = filename
        else:
            # Text field
            fields[name] = payload.decode("utf-8", errors="replace")

    return fields
