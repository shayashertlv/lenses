"""
HTTP request handler and multipart form-data parsers.
"""

import json
import threading
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler

from UI.config import sessions
from UI.config import CATALOG_IMAGES_DIR
from UI.pipelines import (
    run_pipeline, run_free_search_pipeline, run_recolor_pipeline,
    run_storefront_tryon_pipeline, get_catalog_products,
)
from UI.templates import LANDING_HTML, FREE_SEARCH_HTML, LENS_RECOLOR_HTML, STOREFRONT_HTML


class Handler(BaseHTTPRequestHandler):

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path

        if path == "/":
            self._html(LANDING_HTML)
        elif path == "/free-search":
            self._html(FREE_SEARCH_HTML)
        elif path == "/lens-recolor":
            self._html(LENS_RECOLOR_HTML)
        elif path == "/storefront":
            self._html(STOREFRONT_HTML)
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
        for key in ("frame_shape", "frame_color", "frame_material",
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
                "tryon_status": opt["tryon_status"],
                "tryon_b64": opt["tryon_b64"],
                "tryon_error": opt.get("tryon_error"),
            }

        self._json(200, resp)

    # ── helpers ───────────────────────────────────────────────────────
    def _html(self, content: str):
        data = content.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
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
