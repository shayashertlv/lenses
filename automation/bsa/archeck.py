"""S9 AR check: load GLBs in the actual AR runtime (TryOnRenderer) through the QA harness.

Drives ``python -m qa.provider_comparison --manifest M --output O --ar-check`` from ``automation/``
(which runs ``ar/qa/provider-comparison.mjs --stage=ar`` in headless Chromium on the unchanged
bytes). Nothing in ``qa/`` or ``ar/`` is modified; this module only writes a manifest, runs the
harness and reads its ``report.json``.

Per model the result carries the runtime status (``runtime_compatible`` or ``*_rejected`` + error),
the number of optical meshes the runtime detected (``instance.lensMeshes`` - a mesh whose material
has transmission > 0 or a canonical lens descriptor), whether the synthetic fit settled, and the
paths of the render PNGs.

The harness marks the WHOLE run ``failed`` when the page logs any console error, a response >= 400,
a blocked external request or an unstable source snapshot; per-model rows are still reported
(``harness_status`` says which). ``validate_ar_result`` is the ONE reading of a result every consumer
uses: a row is only evidence when the run around it succeeded and the renders it names exist, hash and
cover exactly the requested views. Until 2026-09-27 every consumer judged the rows alone (fail-open).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

import numpy as np

from .core import AUTOMATION

DEFAULT_AR_VIEWS = ({"id": "front", "yaw_degrees": 0}, {"id": "angled", "yaw_degrees": 35})
HARNESS_TIMEOUT_S = 1800
HARNESS_OK_STATUSES = ("rendered", "inspected")     # the harness's own success words (provider-comparison.mjs)


def safe_id(name: str) -> str:
    """The harness requires lower-case [a-z0-9_-] case ids."""
    s = re.sub(r"[^a-z0-9_-]+", "-", str(name).lower()).strip("-_") or "model"
    return s[:80]


def front_width_mm(path: Path) -> float:
    """Full X extent of a GLB in mm (the harness wants a 60-250 mm stated width)."""
    from reconstruction.mesh import load_glb_bytes
    V = load_glb_bytes(Path(path).read_bytes()).vertices
    return float(np.ptp(V[:, 0]) * 1000.0)


def write_manifest(glb_paths: dict[str, str | Path], out_dir: Path, *, ar_views=DEFAULT_AR_VIEWS,
                   environment: dict | None = None, background: str = "checker", shadows: bool = True,
                   photos: dict[str, dict[str, str]] | None = None, description: str | None = None,
                   width_mm: dict[str, float] | None = None, background_color: str | None = None) -> tuple[Path, dict[str, str]]:
    """Write the harness manifest next to the renders. Returns (manifest path, case id -> name).
    ``background`` is the harness fixture (``checker`` or ``solid``); ``background_color`` (#RRGGBB) applies to ``solid``."""
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    cases, ids = [], {}
    for name, p in glb_paths.items():
        p = Path(p).resolve()
        cid = safe_id(name)
        if cid in ids:
            raise ValueError(f"Two models map to the same harness id {cid!r}")
        ids[cid] = name
        w = (width_mm or {}).get(name)
        if w is None:
            try:
                w = front_width_mm(p)
            except Exception:  # noqa: BLE001 - the runtime will report the real load error
                w = 145.0
        w = float(np.clip(w, 60.0, 250.0))
        cases.append({"id": cid, "product": cid, "provider": str(name), "path": str(p),
                      "model_sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "width_mm": round(w, 2)})
    manifest = {"input_description": description or "BSA S9 export AR check (bsa.archeck).",
                "cases": cases, "ar_views": [dict(v) for v in ar_views],
                "background_fixture": background, "shadows": bool(shadows)}
    if background_color is not None:
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", background_color):
            raise ValueError("background_color must be #RRGGBB")
        manifest["background_color"] = background_color
    if environment is not None:
        manifest["environments"] = [dict(environment)]
    if photos:
        manifest["products"] = {safe_id(n): {"photos": {v: str(Path(pp).resolve()) for v, pp in ph.items()}}
                                for n, ph in photos.items()}
    path = out_dir / "manifest.json"
    path.write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    return path, ids


def parse_report(out_dir: Path, ids: dict[str, str]) -> dict:
    """Per-model summary of a harness report.json. ``renders`` are the actual-ar PNG paths (kept for the sheet
    writers); ``render_files`` carry each one's view and the sha256 the harness wrote for its bytes, which
    ``validate_ar_result`` checks against the file."""
    out_dir = Path(out_dir)
    report_path = out_dir / "report.json"
    if not report_path.exists():
        return {"harness_status": "no_report", "harness_errors": [], "source_snapshot_stable": None,
                "models": {n: {"status": "not_run", "runtime_compatible": False} for n in ids.values()}}
    report = json.loads(report_path.read_text(encoding="utf-8"))
    models = {}
    for row in report.get("cases", []):
        name = ids.get(row.get("id"), row.get("id"))
        render_files = [{"view": r.get("view"), "filename": r.get("filename"), "path": str(out_dir / r["filename"]),
                         "sha256": r.get("sha256"), "environment": r.get("environment")}
                        for r in row.get("renders", []) if r.get("mode") == "actual-ar"]
        models[name] = {
            "status": row.get("status"),
            "runtime_compatible": row.get("status") == "runtime_compatible",
            "error": (row.get("error") or "").splitlines()[0] if row.get("error") else None,
            "optical_meshes_detected": row.get("optical_meshes_detected"),
            "lens_mesh_names": sorted({l.get("name") for r in row.get("renders", [])
                                       for l in (r.get("spatial") or {}).get("lenses", [])}),
            "synthetic_fit_ready": row.get("synthetic_fit_ready"),
            "continuity_failure": row.get("continuity_failure"),
            # the key's presence: null means measured and passed, absence a harness that predates the measurement
            # (until 2026-09-28 both read as None and a fresh pass was recorded as 'measured': false)
            "continuity_measured": "continuity_failure" in row,
            "load_milliseconds": row.get("load_milliseconds"),
            "renders": [r["path"] for r in render_files],
            "render_files": render_files,
            "model_sha256": row.get("model_sha256"),
            "file_bytes": row.get("file_bytes"),
        }
    for name in ids.values():
        models.setdefault(name, {"status": "not_run", "runtime_compatible": False})
    return {"harness_status": report.get("status"), "compatible_count": report.get("compatible_count"),
            "rejected_count": report.get("rejected_count"), "harness_errors": report.get("errors", []),
            "source_snapshot_stable": report.get("source_snapshot_stable"), "report_path": str(report_path),
            "models": models}


def _file_sha256(path: str | Path) -> str | None:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def validate_ar_result(result: dict, *, expected_models: dict[str, str | None], expected_views,
                       require_optical: bool = True) -> dict:
    """The one fail-closed reading of a harness result (the ``run`` / ``parse_report`` shape plus ``returncode``).

    Global: returncode 0, harness_status in ``HARNESS_OK_STATUSES``, no harness_errors, source_snapshot_stable True,
    a nonempty model list that is exactly ``expected_models``. Per model: status ``runtime_compatible`` with no
    error, ``model_sha256`` equal to the expected sha when one is given, the actual-ar renders covering EXACTLY
    ``expected_views`` (one existing file per view whose bytes hash to the sha256 the harness wrote; a subset, zero
    views, an extra view or an unhashed render fails) and, with ``require_optical``, at least one optical mesh.
    Nothing here defaults to success: a missing key is a failure, so a legacy or hand-built result is invalid.
    Returns {"ok", "harness_ok", "reasons", "models": {name: {"ok", "reasons"}}}: ``harness_ok`` is the run-level
    verdict alone (``reasons``), ``ok`` = harness_ok and every model ok. A consumer of ONE row of a batch needs
    ``harness_ok and models[name]["ok"]`` (another product's rejection is that product's failure, not the run's)."""
    reasons: list[str] = []
    views = [str(v) for v in (expected_views or [])]
    if result.get("returncode") != 0:
        reasons.append(f"harness returncode {result.get('returncode')!r}")
    if result.get("harness_status") not in HARNESS_OK_STATUSES:
        reasons.append(f"harness status {result.get('harness_status')!r}")
    if result.get("harness_errors"):
        reasons.append(f"harness errors: {len(result['harness_errors'])} (first: {str(result['harness_errors'][0])[:160]})")
    if result.get("source_snapshot_stable") is not True:
        reasons.append(f"source snapshot stable {result.get('source_snapshot_stable')!r}")
    if not expected_models:
        reasons.append("no models expected")
    if not views:
        reasons.append("no views requested")
    if len(set(views)) != len(views):
        reasons.append("duplicate view ids requested")
    models = result.get("models") or {}
    if not models:
        reasons.append("empty result")
    unexpected = sorted(set(models) - set(expected_models or {}))
    if unexpected:
        reasons.append(f"unexpected models {unexpected[:4]}")
    per_model = {}
    for name, sha in (expected_models or {}).items():
        mr: list[str] = []
        row = models.get(name)
        if row is None:
            per_model[name] = {"ok": False, "reasons": ["missing from the report"]}
            continue
        if row.get("status") != "runtime_compatible":
            mr.append(f"status {row.get('status')!r}")
        if row.get("error"):
            mr.append(f"error: {str(row['error'])[:160]}")
        if sha is not None and row.get("model_sha256") != sha:
            mr.append(f"model sha256 {str(row.get('model_sha256'))[:12]} != expected {sha[:12]}")
        if require_optical and (row.get("optical_meshes_detected") or 0) < 1:
            mr.append(f"optical meshes detected {row.get('optical_meshes_detected')!r}")
        files = row.get("render_files")
        if files is None:
            mr.append("renders carry no sha256 (not a parse_report row)")
            files = []
        by_view: dict[str, list[dict]] = {}
        for r in files:
            by_view.setdefault(str(r.get("view")), []).append(r)
        extra = sorted(set(by_view) - set(views))
        if extra:
            mr.append(f"unexpected views {extra[:4]}")
        for v in views:
            got = by_view.get(v, [])
            if len(got) != 1:
                mr.append(f"view {v!r}: {len(got)} renders (need exactly 1)")
                continue
            r = got[0]
            if not r.get("sha256"):
                mr.append(f"view {v!r}: render without sha256")
            elif not r.get("path") or not Path(r["path"]).is_file():
                mr.append(f"view {v!r}: render file missing")
            elif _file_sha256(r["path"]) != r["sha256"]:
                mr.append(f"view {v!r}: render bytes do not match the report's sha256")
        per_model[name] = {"ok": not mr, "reasons": mr}
    harness_ok = not reasons
    ok = harness_ok and bool(per_model) and all(m["ok"] for m in per_model.values())
    return {"ok": ok, "harness_ok": harness_ok, "reasons": reasons, "models": per_model}


def run(glb_paths: dict[str, str | Path], out_dir: str | Path, *, ar_views=DEFAULT_AR_VIEWS,
        environment: dict | None = None, background: str = "checker", shadows: bool = True,
        photos: dict[str, dict[str, str]] | None = None, description: str | None = None,
        width_mm: dict[str, float] | None = None, timeout_s: int = HARNESS_TIMEOUT_S,
        background_color: str | None = None) -> dict:
    """Load each GLB in the actual AR renderer and return per-model status + render paths.

    ``glb_paths``: display name -> GLB path. ``environment``: None = the runtime's native room
    lighting (production), else one harness preset dict (e.g. {"id": "broad", "preset":
    "broad_studio", "intensity": 0.8}). Renders land in ``out_dir``.
    """
    if not glb_paths:
        raise ValueError("No GLBs to check")
    out_dir = Path(out_dir).resolve()
    manifest, ids = write_manifest(glb_paths, out_dir, ar_views=ar_views, environment=environment,
                                   background=background, shadows=shadows, photos=photos,
                                   description=description, width_mm=width_mm, background_color=background_color)
    old = out_dir / "report.json"
    if old.exists():
        old.unlink()  # never read a stale report as this run's result
    cmd = [sys.executable, "-m", "qa.provider_comparison", "--manifest", str(manifest), "--output", str(out_dir), "--ar-check"]
    try:
        proc = subprocess.run(cmd, cwd=str(AUTOMATION), capture_output=True, text=True, timeout=timeout_s,
                              encoding="utf-8", errors="replace")
        returncode, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as e:
        returncode, stdout, stderr = None, str(e.stdout or ""), f"timeout after {timeout_s}s"
    result = parse_report(out_dir, ids)
    result.update({"returncode": returncode, "command": cmd, "manifest_path": str(manifest), "out_dir": str(out_dir),
                   "stderr_tail": stderr[-2000:] if stderr else "", "stdout_tail": stdout[-1500:] if stdout else ""})
    # the manifest's sha256 is the hash of the bytes we handed over; the report's row must name the same bytes
    expected = {ids[c["id"]]: c.get("model_sha256") for c in json.loads(manifest.read_text(encoding="utf-8"))["cases"]}
    result["validation"] = validate_ar_result(result, expected_models=expected, expected_views=[v["id"] for v in ar_views])
    # the key predates the validator; its meaning is now the validator's verdict (a failed run, an unstable snapshot
    # or a missing render is not compatibility, whatever the rows say)
    result["all_compatible_with_lenses"] = bool(result["validation"]["ok"])
    (out_dir / "archeck.json").write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    return result


# --------------------------------------------------------------------------- review sheets
def checker_background(width: int = 720, height: int = 480) -> np.ndarray:
    """The harness's controlled checker fixture (provider-comparison-ar.html)."""
    yy, xx = np.mgrid[0:height, 0:width]
    odd = ((xx // 32) + (yy // 32)) % 2 == 1
    out = np.empty((height, width, 3), np.uint8)
    out[odd] = (0xB4, 0xA1, 0x90)
    out[~odd] = (0xEB, 0xE0, 0xD2)
    return out


def content_box(paths, margin: int = 12, threshold: int = 14) -> tuple[int, int, int, int]:
    """Union bbox of the pixels that differ from the checker fixture over several renders."""
    from PIL import Image
    box = None
    for p in paths:
        im = np.asarray(Image.open(p).convert("RGB"))
        bg = checker_background(im.shape[1], im.shape[0])
        diff = (np.abs(im.astype(int) - bg.astype(int)).max(-1) > threshold)
        ys, xs = np.nonzero(diff)
        if not len(xs):
            continue
        b = (xs.min(), ys.min(), xs.max() + 1, ys.max() + 1)
        box = b if box is None else (min(box[0], b[0]), min(box[1], b[1]), max(box[2], b[2]), max(box[3], b[3]))
    if box is None:
        return (0, 0, 720, 480)
    return (max(0, box[0] - margin), max(0, box[1] - margin), box[2] + margin, box[3] + margin)


def review_sheet(rows: list[tuple[str, list[str]]], out_path: str | Path, *, scale: float = 2.0,
                 title: str | None = None) -> str:
    """rows: [(label, [render png, ...]), ...] -> one PNG, each column cropped to the union content box."""
    from PIL import Image, ImageDraw, ImageFont
    ncol = max(len(r[1]) for r in rows)
    boxes = [content_box([r[1][c] for r in rows if c < len(r[1]) and Path(r[1][c]).exists()]) for c in range(ncol)]
    widths = [int((b[2] - b[0]) * scale) for b in boxes]
    heights = [int((b[3] - b[1]) * scale) for b in boxes]
    rh, lw, th = max(heights), 260, 40 if title else 0
    sheet = Image.new("RGB", (lw + sum(widths), th + rh * len(rows)), "white")
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 18)
    except OSError:
        font = ImageFont.load_default()
    if title:
        draw.text((10, 10), title, fill="black", font=font)
    for r, (label, paths) in enumerate(rows):
        y = th + r * rh
        draw.multiline_text((8, y + 8), label, fill="black", font=font)
        x = lw
        for c in range(ncol):
            if c < len(paths) and Path(paths[c]).exists():
                im = Image.open(paths[c]).convert("RGB").crop(boxes[c])
                im = im.resize((widths[c], heights[c]), Image.LANCZOS)
                sheet.paste(im, (x, y))
            x += widths[c]
    out_path = Path(out_path)
    sheet.save(out_path)
    return str(out_path)


def photo_render_sheet(rows: list[tuple[str, list[str], list[str]]], out_path: str | Path, *, height: int = 300,
                       title: str | None = None) -> str:
    """rows: [(label, [photo paths], [render paths])] -> PNG; photos scaled to ``height``, renders cropped to
    their content box (checker fixture) and scaled to the same height."""
    from PIL import Image, ImageDraw, ImageFont
    tiles_per_row = []
    for label, photos, renders in rows:
        tiles = []
        for p in photos:
            if p and Path(p).exists():
                im = Image.open(p).convert("RGB")
                tiles.append(im.resize((max(1, round(im.width * height / im.height)), height), Image.LANCZOS))
        for p in renders:
            if p and Path(p).exists():
                im = Image.open(p).convert("RGB").crop(content_box([p], margin=10))
                tiles.append(im.resize((max(1, round(im.width * height / im.height)), height), Image.LANCZOS))
        tiles_per_row.append((label, tiles))
    lw, th = 200, 40 if title else 0
    W = lw + max([sum(t.width for t in tl) + 6 * len(tl) for _, tl in tiles_per_row] + [400])
    sheet = Image.new("RGB", (W, th + (height + 8) * len(rows)), "white")
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 18)
    except OSError:
        font = ImageFont.load_default()
    if title:
        draw.text((10, 10), title, fill="black", font=font)
    for r, (label, tiles) in enumerate(tiles_per_row):
        y = th + r * (height + 8)
        draw.multiline_text((8, y + 8), label, fill="black", font=font)
        x = lw
        for t in tiles:
            sheet.paste(t, (x, y))
            x += t.width + 6
    sheet.save(out_path)
    return str(out_path)
