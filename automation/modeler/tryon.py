"""Live try-on of the modeler jobs' delivered GLBs in the AR app (ar/, dev server on 8240): the legacy jobs under
data/modeler/jobs, the modeler.agentic jobs under data/modeler/agentic (deliverable/manifest.json) and the no-inference
rebuilds there (`python -m modeler.agentic rebuild`: a folder whose manifest.json has kind 'preview_rebuild'), listed
beside the deliveries as 'rebuild of <job> <rid> with the current library, not a delivery', and the candidates an agentic job
holds for the owner's review (state awaiting_owner, ``review/candidate.json``), labelled 'awaiting your review'.

    python -m modeler.tryon [port] [--jobs name,name]      (default port 8793; then open http://127.0.0.1:<port>/)

``--jobs`` takes job names under data/modeler/jobs or data/modeler/agentic, or absolute job folders anywhere.

Serves ONLY an allowlist on 127.0.0.1 with CORS: per finished job the delivered GLB named by its manifest, the job's
same-protocol baselines (previous routes, for comparison) and the front photo for the index. Each link opens the AR
app through its validated external-model handover (ar/src/eyewear/external.ts) with the manifest's mounting values:
width = measured front width, temple clip = recommended clip, sha256 = the delivered asset's digest (temple
continuity). Nothing is written; no network beyond loopback.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import http.server
import json
import sys
from pathlib import Path
from urllib.parse import quote, urlencode

from .paths import AUTOMATION, JOBS, MODELER_DATA

AR_APP = "http://127.0.0.1:8240/"
AGENTIC = MODELER_DATA / "agentic"


def _load(p: Path):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _resolve(p: str | Path) -> Path:
    p = Path(p)
    return p if p.is_absolute() else (AUTOMATION / p)


def tryon_link(*, route: str, name: str, width_mm: float, clip_zm: float, sha256: str, port: int = 8793, ar_app: str = AR_APP) -> str:
    """The AR app's external-model handover URL for a GLB this server serves at ``route``."""
    params = {"model": f"http://127.0.0.1:{port}{route}", "name": name, "clip": clip_zm, "width": width_mm, "sha256": sha256}
    return f"{ar_app}?{urlencode(params, quote_via=quote)}"


def review_route(job: str, revision: str) -> str:
    """Where this server serves an agentic job's review candidate (never the delivered file's route)."""
    return f"/models/{job}/review-{revision}.glb"


def width_from_bbox(bbox) -> float:
    """The front width (mm) of a revision's observation bbox_mm, clamped as the AR app accepts it; 140 without one."""
    try:
        return round(min(250.0, max(60.0, float(bbox[1][0] - bbox[0][0]))), 1)
    except (TypeError, ValueError, IndexError):
        return 140.0


def job_rows(job_dir: Path, port: int, ar_app: str = AR_APP) -> list[dict]:
    """The delivered asset of one job plus its baselines, each with a ready try-on URL."""
    m = _load(job_dir / "manifest.json")
    if not m or not m.get("asset"):
        return []
    job = job_dir.name
    product = m.get("product_id", job)
    mount = m.get("mounting") or {}
    rows = []
    glb = _resolve(m["asset"]["path"])
    if glb.is_file():
        width = float(mount.get("front_width_mm_measured") or 140.0)
        clip = float(mount.get("temple_clip_z_m_recommended") or -0.14)
        ev = (m.get("evaluation") or {}).get("overall")
        rows.append({"product": product, "job": job, "kind": "delivered", "path": glb,
                     "label": f"{job}: delivered {m.get('delivered_candidate')} ({m.get('status')}"
                              + (f", evaluator {ev}" if ev else "") + ")",
                     "route": f"/models/{job}/{glb.name}", "width_mm": round(width, 1), "clip_zm": clip,
                     "sha256": m["asset"].get("sha256") or hashlib.sha256(glb.read_bytes()).hexdigest()})
    for b in sorted(job_dir.glob("baselines/*/baseline.json")):
        bj = _load(b) or {}
        bp = bj.get("glb")
        if not bp:
            continue
        bp = _resolve(bp)
        if not bp.is_file():
            continue
        try:
            from bsa.archeck import front_width_mm
            width = min(250.0, max(60.0, front_width_mm(bp)))
        except Exception:  # noqa: BLE001 - a baseline without a readable width still gets a link
            width = 140.0
        rows.append({"product": product, "job": job, "kind": "baseline", "path": bp,
                     "label": f"{job}: baseline {bj.get('name', b.parent.name)} (previous route)",
                     "route": f"/models/{job}/baseline-{b.parent.name}.glb", "width_mm": round(width, 1),
                     "clip_zm": -0.14, "sha256": hashlib.sha256(bp.read_bytes()).hexdigest()})
    lensenv = mount.get("lens_env_intensity_recommended")
    for r in rows:
        url = f"http://127.0.0.1:{port}{r['route']}"
        params = {"model": url, "name": f"{product} - {r['label']}", "clip": r["clip_zm"], "width": r["width_mm"], "sha256": r["sha256"]}
        if r["kind"] == "delivered" and lensenv is not None:
            params["lensenv"] = lensenv          # the mirror coat's brightness the photo implies (manifest mounting)
            r["label"] += f", lens env x{lensenv}"
        query = urlencode(params, quote_via=quote)
        r["try_on"] = f"{ar_app}?{query}"
        r["bytes"] = r["path"].stat().st_size
    photo = next((i for i in (m.get("evidence") or {}).get("inputs", []) if i.get("view") == "front"), None)
    if photo and _resolve(photo["path"]).is_file():
        for r in rows:
            r["photo"] = _resolve(photo["path"])
    return rows


def agentic_rows(job_dir: Path, port: int, ar_app: str = AR_APP) -> list[dict]:
    """The delivered asset of one modeler.agentic job (deliverable/manifest.json, byte-bound), with the product, the measured front
    width and the front photo read from the job database when it can be opened; the card links the bytes either way."""
    m = _load(job_dir / "deliverable" / "manifest.json") or {}
    asset = m.get("asset") or {}
    if not asset.get("path"):
        return []
    glb = _resolve(asset["path"])
    if not glb.is_file():
        return []
    job = job_dir.name
    product, width, photo = job, 140.0, None
    try:
        from .agentic.state import Store
        store = Store.open(job_dir, readonly=True)
        try:
            request = store.job()["request"]
            product = request.get("product_id") or job
            rev = store.revision(asset["revision"]) if asset.get("revision") else None
            bbox = ((rev or {}).get("observation") or {}).get("bbox_mm")
            if bbox:
                width = float(bbox[1][0] - bbox[0][0])
            front = next((p for p in request.get("photos", []) if p.get("view") == "front"), None)
            if front and Path(front["path"]).is_file():
                photo = Path(front["path"])
        finally:
            store.close()
    except Exception:  # noqa: BLE001 - an unreadable database still leaves a usable link to the delivered bytes
        pass
    visual = (m.get("axes") or {}).get("visual") or {}
    label = (f"{job}: agentic {asset.get('revision')} ({m.get('deliverable_status')}, {visual.get('status')}"
             + (f", evaluator {visual.get('evaluator_overall')}" if visual.get("evaluator_overall") else "") + ")")
    row = {"product": product, "job": job, "kind": "delivered", "path": glb, "label": label, "route": f"/models/{job}/{glb.name}",
           "width_mm": round(min(250.0, max(60.0, width)), 1), "clip_zm": -0.14,
           "sha256": asset.get("sha256") or hashlib.sha256(glb.read_bytes()).hexdigest()}
    params = {"model": f"http://127.0.0.1:{port}{row['route']}", "name": f"{product} - {row['label']}", "clip": row["clip_zm"], "width": row["width_mm"],
              "sha256": row["sha256"]}
    row["try_on"] = f"{ar_app}?{urlencode(params, quote_via=quote)}"
    row["bytes"] = glb.stat().st_size
    if photo is not None:
        row["photo"] = photo
    return [row]


def _is_agentic(d: Path) -> bool:
    return (d / "deliverable" / "manifest.json").exists() and (d / "job.sqlite3").exists()


REVIEW_KIND = "review_candidate"        # modeler.agentic.evaluation.REVIEW_KIND


def _is_awaiting(d: Path) -> bool:
    return (d / "review" / "candidate.json").exists() and (d / "job.sqlite3").exists()


def review_rows(job_dir: Path, port: int, ar_app: str = AR_APP) -> list[dict]:
    """The candidate an agentic job holds for the owner's review, labelled 'awaiting your review': listed only while the job is in
    state awaiting_owner with that round still open, and only when the file's bytes are the candidate's (a synthetic candidate
    has no GLB and is not listed)."""
    m = _load(job_dir / "review" / "candidate.json") or {}
    asset = m.get("asset") or {}
    if m.get("kind") != REVIEW_KIND or not asset.get("path") or not asset.get("sha256"):
        return []
    glb = _resolve(asset["path"])
    if not glb.is_file() or hashlib.sha256(glb.read_bytes()).hexdigest() != asset["sha256"]:
        return []
    try:
        from .agentic.state import Store
        store = Store.open(job_dir, readonly=True)
        try:
            job = store.job()
            open_round = store.open_owner_round()
        finally:
            store.close()
    except Exception:  # noqa: BLE001 - an unreadable database lists nothing: a candidate is shown only while it is awaited
        return []
    if job["state"] != "awaiting_owner" or open_round is None or open_round["round"] != m.get("round"):
        return []
    rid = (m.get("revision") or {}).get("id") or asset.get("revision")
    tryon = m.get("tryon") or {}
    product = m.get("product_id") or job_dir.name
    label = f"{job_dir.name}: {rid} round {m.get('round')}, awaiting your review"
    width = float(tryon.get("width_mm") or 140.0)
    clip = float(tryon.get("clip_zm") or -0.14)
    route = review_route(job_dir.name, rid)
    row = {"product": product, "job": job_dir.name, "kind": "review", "path": glb, "label": label, "route": route, "width_mm": width, "clip_zm": clip,
           "sha256": asset["sha256"], "round": m.get("round"), "job_dir": str(job_dir)}
    row["try_on"] = tryon_link(route=route, name=f"{product} - {label}", width_mm=width, clip_zm=clip, sha256=asset["sha256"], port=port, ar_app=ar_app)
    row["bytes"] = glb.stat().st_size
    return [row]


PREVIEW_KIND = "preview_rebuild"        # modeler.agentic.rebuild.PREVIEW_KIND (not imported: listing needs no agentic import)


def _is_preview(d: Path) -> bool:
    try:
        return (_load(d / "manifest.json") or {}).get("kind") == PREVIEW_KIND
    except (OSError, ValueError):
        return False


def preview_rows(out_dir: Path, port: int, ar_app: str = AR_APP) -> list[dict]:
    """A no-inference rebuild's preview GLB (``<folder>/model.glb``, byte-bound to its manifest), labelled as a rebuild of the
    source job's revision with the current library and never as a delivery. Nothing is listed when the bytes changed or the
    rebuild produced no asset (a synthetic worker, a failed build)."""
    m = _load(out_dir / "manifest.json") or {}
    asset = m.get("asset") or {}
    if m.get("kind") != PREVIEW_KIND or not asset.get("path") or not asset.get("sha256"):
        return []
    glb = out_dir / Path(asset["path"]).name
    if not glb.is_file() or hashlib.sha256(glb.read_bytes()).hexdigest() != asset["sha256"]:
        return []
    source = m.get("source") or {}
    comp = m.get("compatibility") or {}
    folder = out_dir.name
    label = (f"{folder}: {m.get('label') or 'rebuild with the current library, not a delivery'} ("
             + ("compatible" if comp.get("compatible") else f"not compatible: {comp.get('reasons')}") + ")")
    width = float(m.get("width_mm") or 140.0)
    row = {"product": source.get("product_id") or folder, "job": folder, "kind": "preview", "path": glb, "label": label,
           "route": f"/models/{folder}/{glb.name}", "width_mm": round(min(250.0, max(60.0, width)), 1), "clip_zm": -0.14, "sha256": asset["sha256"]}
    params = {"model": f"http://127.0.0.1:{port}{row['route']}", "name": f"{row['product']} - {label}", "clip": row["clip_zm"], "width": row["width_mm"],
              "sha256": row["sha256"]}
    row["try_on"] = f"{ar_app}?{urlencode(params, quote_via=quote)}"
    row["bytes"] = glb.stat().st_size
    photo = source.get("front_photo")
    if photo and Path(photo).is_file():
        row["photo"] = Path(photo)
    return [row]


def _job_dir(j: str) -> Path:
    p = Path(j)
    if p.is_absolute():
        return p
    return (JOBS / j) if (JOBS / j).exists() else (AGENTIC / j)


def _rows_of(d: Path, port: int) -> list[dict]:
    if _is_agentic(d) or _is_awaiting(d):
        return (agentic_rows(d, port) if _is_agentic(d) else []) + (review_rows(d, port) if _is_awaiting(d) else [])
    return preview_rows(d, port) if _is_preview(d) else job_rows(d, port)


def catalog(jobs: list[str] | None, port: int) -> list[dict]:
    if jobs:
        dirs = [_job_dir(j) for j in jobs]
    else:
        dirs = sorted(p for p in JOBS.iterdir() if (p / "manifest.json").exists()) if JOBS.is_dir() else []
        dirs += sorted(p for p in AGENTIC.iterdir() if p.is_dir() and (_is_agentic(p) or _is_awaiting(p) or _is_preview(p))) if AGENTIC.is_dir() else []
    rows, seen = [], set()
    for d in dirs:
        for r in _rows_of(d, port):
            key = (r["product"], r["path"].resolve())
            if key in seen:
                continue  # the same baseline GLB measured by several jobs of one product: one link
            seen.add(key)
            rows.append(r)
    return rows


def index_page(rows: list[dict]) -> bytes:
    by: dict[str, list[dict]] = {}
    for r in rows:
        by.setdefault(r["product"], []).append(r)
    cards = []
    for product, prows in by.items():
        photo = next((f"/photos/{r['job']}.jpg" for r in prows if r.get("photo")), None)
        links = "".join(
            f'<a class="try {r["kind"]}" href="{html.escape(r["try_on"])}" target="_blank" rel="noopener">'
            f'<b>{html.escape(r["label"])}</b><span>{r["width_mm"]} mm wide, clip {r["clip_zm"]} m, {r["bytes"] / 1e6:.1f} MB</span></a>'
            for r in sorted(prows, key=lambda r: (r["kind"] not in ("review", "delivered"), r["kind"] != "review", r["job"])))
        img = f'<img src="{photo}" alt="{html.escape(product)} front photo">' if photo else ""
        cards.append(f'<section>{img}<div><h2>{html.escape(product)}</h2>{links}</div></section>')
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Modeler Try-On</title><style>
:root{{--bg:#f6f5f2;--fg:#1d1d1f;--muted:#6b6b70;--card:#fff;--line:#e3e1dc;--accent:#2f5d8a}}
@media (prefers-color-scheme:dark){{:root{{--bg:#16171a;--fg:#ececef;--muted:#a0a0a8;--card:#1f2024;--line:#33343a;--accent:#8db4dc}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}}
main{{max-width:980px;margin:0 auto;padding:24px 16px 60px}} h1{{font-size:22px;margin:0 0 4px}} p{{color:var(--muted);margin:0 0 20px}}
section{{display:flex;gap:16px;align-items:center;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px;margin:0 0 14px}}
section img{{width:200px;max-width:35%;background:#fff;border-radius:6px}} h2{{margin:0 0 8px;font-size:17px;text-transform:uppercase;letter-spacing:.04em}}
a.try{{display:block;text-decoration:none;color:var(--fg);border:1px solid var(--line);border-radius:8px;padding:8px 12px;margin:0 0 8px}}
a.try:hover{{border-color:var(--accent)}} a.try b{{color:var(--accent);display:block}} a.try span{{color:var(--muted);font-size:13px}}
a.baseline b{{color:var(--muted)}} a.preview{{border-style:dashed}} a.review{{border-color:var(--accent);border-width:2px}}
@media (max-width:600px){{section{{flex-direction:column;align-items:stretch}} section img{{width:100%;max-width:none}}}}
</style></head><body><main><h1>Modeler try-on</h1>
<p>Each link opens the AR app (dev server on 8240) with that GLB; allow the camera. Width, temple clip and digest come from the job manifest.
Dashed links are no-inference rebuilds with the current library: previews, not deliveries. Outlined links are candidates
awaiting your review: decide with <code>python -m modeler.agentic owner-review</code>.
Scale is nominal (140 mm front assumed) unless the request stated a dimension.</p>
{"".join(cards)}</main></body></html>"""
    return page.encode("utf-8")


def serve(port: int = 8793, jobs: list[str] | None = None) -> None:
    rows = catalog(jobs, port)
    if not rows:
        raise SystemExit(f"no finished job with a delivered asset under {JOBS} or {AGENTIC}")
    files = {r["route"]: r["path"] for r in rows}
    files.update({f"/photos/{r['job']}.jpg": r["photo"] for r in rows if r.get("photo")})
    index = index_page(rows)

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
            if path == "/catalog.json":
                body = json.dumps([{k: (str(v) if isinstance(v, Path) else v) for k, v in r.items()} for r in rows], indent=1)
                return self._send(200, body.encode("utf-8"), "application/json")
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
            sys.stderr.write("[modeler.tryon] " + fmt % args + "\n")

    for r in rows:
        print(f"{r['product']:6s} {r['kind']:9s} {r['width_mm']:6.1f} mm  {r['try_on']}")
    print(f"index: http://127.0.0.1:{port}/", flush=True)
    http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("port", nargs="?", type=int, default=8793)
    ap.add_argument("--jobs", help="comma-separated job names (default: every finished job)")
    args = ap.parse_args(argv)
    serve(args.port, [j for j in (args.jobs or "").split(",") if j] or None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
