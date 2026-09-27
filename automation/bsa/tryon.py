"""Live try-on of a run's models in the AR app (ar/, dev server on 8240) through its external-model handover.

    python -m bsa.tryon [run] [port]        (defaults: m3, 8792; then open http://127.0.0.1:<port>/)

Serves ONLY an allowlist - per product the S11 look (when present), the S9 export and the previous route's candidate,
plus the front photos for the index - on 127.0.0.1 with CORS, so the AR page (another loopback origin) may fetch them.
Each link carries the handover the AR harness uses for the same model (ar/qa/provider-comparison-ar.html): width =
the GLB's full X extent (archeck.front_width_mm), temple clip -0.14 m, and the asset's SHA-256 (temple continuity).
Nothing is written; no network beyond loopback.
"""
from __future__ import annotations

import hashlib
import html
import http.server
import json
import sys
from pathlib import Path
from urllib.parse import quote, urlencode

import numpy as np

from .archeck import front_width_mm
from .core import BSA_DATA, PRODUCTS

AR_APP = "http://127.0.0.1:8240/"
HARNESS_CLIP_ZM = -0.14                  # the clip the AR harness registers every checked model with
VARIANTS = (("look", "BSA + Astra look (S11)"), ("bsa", "BSA export (S9)"), ("previous", "Previous route"))


def model_paths(run: str) -> dict[tuple[str, str], Path]:
    rd = BSA_DATA / "runs" / run
    out = {}
    for p, prod in PRODUCTS.items():
        for key, path in (("look", rd / p / "s11_look" / "model.glb"), ("bsa", rd / p / "s9_export" / "model.glb"),
                          ("previous", prod.candidate_glb)):
            if path is not None and Path(path).is_file():
                out[(p, key)] = Path(path).resolve()
    return out


def look_note(run: str, product: str) -> str:
    r = BSA_DATA / "runs" / run / product / "s11_look" / "result.json"
    if not r.exists():
        return ""
    res = json.loads(r.read_text())
    driver = res.get("driver")
    driver = driver.get("name", "?") if isinstance(driver, dict) else driver
    kept = " = unedited export" if res.get("final_revision") == "r0000" else ""
    return f"{driver}: {res.get('verdict', '?')}, {res.get('final_revision', '?')}{kept}"


def catalog(run: str, port: int, ar_app: str = AR_APP) -> list[dict]:
    rows = []
    for (p, key), path in model_paths(run).items():
        data = path.read_bytes()
        width = float(np.clip(front_width_mm(path), 60.0, 250.0))
        label = dict(VARIANTS)[key]
        url = f"http://127.0.0.1:{port}/models/{p}/{key}.glb"
        query = urlencode({"model": url, "name": f"{p} - {label}", "clip": HARNESS_CLIP_ZM,
                           "width": round(width, 1), "sha256": hashlib.sha256(data).hexdigest()}, quote_via=quote)
        rows.append({"product": p, "variant": key, "label": label, "path": path, "width_mm": round(width, 1),
                     "bytes": len(data), "try_on": f"{ar_app}?{query}",
                     "note": look_note(run, p) if key == "look" else ""})
    return rows


def index_page(run: str, rows: list[dict]) -> bytes:
    by = {}
    for r in rows:
        by.setdefault(r["product"], []).append(r)
    cards = []
    for p in PRODUCTS:
        if p not in by:
            continue
        links = "".join(
            f'<a class="try" href="{html.escape(r["try_on"])}" target="_blank" rel="noopener">'
            f'<b>{html.escape(r["label"])}</b><span>{r["width_mm"]} mm wide, {r["bytes"] / 1e6:.1f} MB'
            f'{(" - " + html.escape(r["note"])) if r["note"] else ""}</span></a>'
            for r in sorted(by[p], key=lambda r: [k for k, _ in VARIANTS].index(r["variant"])))
        cards.append(f'<section><img src="/photos/{p}.jpg" alt="{p} front photo"><div><h2>{p}</h2>{links}</div></section>')
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>BSA Try-On</title><style>
:root{{--bg:#f6f5f2;--fg:#1d1d1f;--muted:#6b6b70;--card:#fff;--line:#e3e1dc;--accent:#2f5d8a}}
@media (prefers-color-scheme:dark){{:root{{--bg:#16171a;--fg:#ececef;--muted:#a0a0a8;--card:#1f2024;--line:#33343a;--accent:#8db4dc}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}}
main{{max-width:980px;margin:0 auto;padding:24px 16px 60px}} h1{{font-size:22px;margin:0 0 4px}} p{{color:var(--muted);margin:0 0 20px}}
section{{display:flex;gap:16px;align-items:center;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px;margin:0 0 14px}}
section img{{width:200px;max-width:35%;background:#fff;border-radius:6px}} h2{{margin:0 0 8px;font-size:17px;text-transform:uppercase;letter-spacing:.04em}}
a.try{{display:block;text-decoration:none;color:var(--fg);border:1px solid var(--line);border-radius:8px;padding:8px 12px;margin:0 0 8px}}
a.try:hover{{border-color:var(--accent)}} a.try b{{color:var(--accent);display:block}} a.try span{{color:var(--muted);font-size:13px}}
@media (max-width:600px){{section{{flex-direction:column;align-items:stretch}} section img{{width:100%;max-width:none}}}}
</style></head><body><main><h1>Try-on, run {html.escape(run)}</h1>
<p>Each link opens the AR app (dev server on 8240) with that model; allow the camera. Same width and temple clip as the AR check renders.</p>
{"".join(cards)}</main></body></html>"""
    return page.encode("utf-8")


def serve(run: str = "m3", port: int = 8792) -> None:
    rows = catalog(run, port)
    files = {f"/models/{r['product']}/{r['variant']}.glb": r["path"] for r in rows}
    files.update({f"/photos/{p}.jpg": PRODUCTS[p].photo_path("front") for p in {r["product"] for r in rows}})
    index = index_page(run, rows)

    class Handler(http.server.BaseHTTPRequestHandler):
        def _send(self, code, body=b"", ctype="text/plain"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cross-Origin-Resource-Policy", "cross-origin")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                return self._send(200, index, "text/html; charset=utf-8")
            f = files.get(path)
            if f is None:
                return self._send(404, b"not found")
            ctype = "model/gltf-binary" if path.endswith(".glb") else "image/jpeg"
            return self._send(200, Path(f).read_bytes(), ctype)

        do_HEAD = do_GET

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, HEAD")
            self.end_headers()

        def log_message(self, fmt, *args):
            sys.stderr.write("[tryon] " + fmt % args + "\n")

    for r in rows:
        print(f"{r['product']:7s} {r['variant']:8s} {r['width_mm']:6.1f} mm  {r['try_on']}")
    print(f"index: http://127.0.0.1:{port}/", flush=True)
    http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    serve(sys.argv[1] if len(sys.argv) > 1 else "m3", int(sys.argv[2]) if len(sys.argv) > 2 else 8792)
