"""Fixed-size AR captures for job tools; the capture and Chromium share a kill-on-close process job."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

from PIL import Image, ImageOps, ImageDraw

from .. import procs
from .lens_response import background_response, response_summary

ARVIEW = Path(__file__).resolve().parents[2] / "arview"
EXIT_STATUS = {0: "ok", 1: "infrastructure_failed", 2: "usage_error", 3: "spec_failed", 4: "refused"}
# D2: renderer.ts uses scene intensity 0.8; lensenv=1 preserves that native appearance for every product.
LENS_ENV_INTENSITY = 1
AR_APPEARANCE_POLICY = "g3d.ar_appearance/1"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def confined(root: Path, path, *, areas=("images", "reference_images", "work")) -> Path:
    """Resolve links before checking containment; money/session/credential files are never image inputs."""
    root = Path(root).resolve()
    target = (root / path).resolve()
    if not any(target.is_relative_to(root / area) and target != root / area for area in areas):
        raise ValueError("path must stay inside the job's image or work directories")
    return target


def output_dir(root: Path, call_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}", call_id):
        raise ValueError("invalid call id")
    path = confined(root, Path("work/arview") / call_id)
    path.mkdir(parents=True, exist_ok=False)
    return path


def run_capture(argv: list[str], out: Path, *, timeout_s: float) -> tuple[int | None, bool]:
    """Own the entire tree through normal exit and timeout; stdout streams to disk, never an unread pipe."""
    env = {key: value for key, value in os.environ.items() if key.upper() in procs.WORKER_ENV_KEYS}
    env.update(OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1")
    proc = job = None
    with (out / "capture.log").open("ab") as log:
        try:
            proc, job = procs.spawn_in_job(argv, cwd=ARVIEW, stdout=log, env=env)
            try:
                return proc.wait(timeout=timeout_s), False
            except subprocess.TimeoutExpired:
                if job.handle is not None:
                    job.terminate()
                else:  # the POSIX process group contains Chromium as well as Node
                    import signal
                    os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=10)
                return None, True
        finally:
            if job is not None:
                job.close()


def contact_sheet(out: Path, records: list[dict]) -> Path | None:
    if not records:
        return None
    worn = [rec for rec in records if rec.get("kind") == "worn_crop"]
    portrait = next((rec for rec in records if rec.get("kind") == "portrait" and
                     rec.get("variant") == "condition-a" and rec.get("region") == "eyes"), None)
    checker = next((rec for rec in records if rec.get("kind") == "pose" and rec.get("pose") == "front" and
                    rec.get("background") == "checker" and rec.get("variant") == "beauty"), None)
    if portrait or checker:
        # One image within the tool's 1024 limit. Eye detail has the largest top tile; native images remain intact.
        standard = [rec for rec in records if rec.get("kind") == "pose" and rec.get("variant") == "beauty"
                    and rec.get("background") == "skin" and rec.get("pose") not in {r.get("pose") for r in worn}]
        height = 328 + len(worn) * 210 + ((len(standard) + 3) // 4) * 132
        sheet = Image.new("RGB", (1024, height), "#ececec")
        draw = ImageDraw.Draw(sheet)

        def tile(rec, box, label):
            if rec is None:
                return
            x, y, width, height = box
            with Image.open(out / rec["file"]) as opened:
                im = ImageOps.contain(ImageOps.exif_transpose(opened).convert("RGB"), (width, height - 24))
            sheet.paste(im, (x + (width - im.width) // 2, y + (height - 24 - im.height) // 2))
            draw.text((x + 6, y + height - 20), label, fill="black")

        tile(portrait, (0, 0, 640, 328), "Front test face: judge eye visibility and lens colour")
        tile(checker, (640, 0, 384, 328), "Front checker: judge see-through detail")
        for i, rec in enumerate(worn):
            tile(rec, (0, 328 + i * 210, 1024, 210), str(rec.get("pose")) + " worn, head visible, hair off")
        for i, rec in enumerate(standard):
            tile(rec, (i % 4 * 256, 328 + len(worn) * 210 + i // 4 * 132, 256, 132), str(rec.get("pose")) + " beauty")
        path = out / "contact-sheet.jpg"
        sheet.save(path, quality=95)
        return path
    if worn:
        # A call emits one <=1024 image: give the two requested close views real pixel space, then the seven
        # standard beauty poses. Native beauty/part-id panels all remain hash-bound in report.json and delivery.
        standard = [rec for rec in records if rec.get("kind") == "pose" and rec.get("variant") == "beauty"
                    and rec.get("pose") not in {item.get("pose") for item in worn}]
        width, row_height, cols = 1024, 328, 4
        bottom_rows = (len(standard) + cols - 1) // cols
        sheet = Image.new("RGB", (width, len(worn) * row_height + bottom_rows * 172), "#ececec")
        draw = ImageDraw.Draw(sheet)
        for i, rec in enumerate(worn + standard):
            close = i < len(worn)
            tile_w, tile_h = (width, 300) if close else (width // cols, 144)
            index = i - len(worn)
            x = 0 if close else index % cols * tile_w
            y = i * row_height if close else len(worn) * row_height + index // cols * 172
            with Image.open(out / rec["file"]) as opened:
                tile = ImageOps.contain(ImageOps.exif_transpose(opened).convert("RGB"), (tile_w, tile_h))
            sheet.paste(tile, (x + (tile_w - tile.width) // 2, y + (tile_h - tile.height) // 2))
            draw.text((x + 6, y + tile_h + 4), str(rec.get("pose", "")) + (" worn, head visible, hair off" if close else " beauty"), fill="black")
        path = out / "contact-sheet.jpg"
        sheet.save(path, quality=95)
        return path
    width, height, cols = 400, 300, min(4, len(records))
    rows = (len(records) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * width, rows * (height + 28)), "#ececec")
    draw = ImageDraw.Draw(sheet)
    for i, rec in enumerate(records):
        with Image.open(out / rec["file"]) as opened:
            tile = ImageOps.contain(ImageOps.exif_transpose(opened).convert("RGB"), (width, height))
        x, y = i % cols * width, i // cols * (height + 28)
        sheet.paste(tile, (x + (width - tile.width) // 2, y + (height - tile.height) // 2))
        draw.text((x + 6, y + height + 4), str(rec.get("pose", "")) + " " + str(rec.get("variant", "")), fill="black")
    path = out / "contact-sheet.jpg"
    sheet.save(path, quality=95)
    return path


def capture(root: Path, export: dict, call_id: str, *, timeout_s=300.0) -> dict:
    """Existing poses/worn sides plus pinned portrait, front checker and informative background response."""
    if export.get("status") != "exported":
        return {"status": "refused", "code": "E_EXPORT", "export": export}
    model = confined(root, export["path"])
    if sha256(model) != export["sha256"]:
        return {"status": "refused", "code": "E_EXPORT", "message": "export bytes changed"}
    node = shutil.which("node")
    if node is None:
        return {"status": "infrastructure_failed", "code": "E_NODE", "message": "Node.js is not available"}
    out = output_dir(root, call_id)
    argv = [node, str(ARVIEW / "bin/arview.mjs"), "capture", str(model), "--out", str(out),
            "--poses", "default", "--backgrounds", "skin", "--fixed-scale", "--worn-sides", "--lens-evidence",
            "--lensenv", str(LENS_ENV_INTENSITY)]
    # The CLI requires an empty directory; put its log outside the capture directory until it has started.
    log_dir = out.with_name(out.name + "-process")
    log_dir.mkdir(exist_ok=False)
    code, timed_out = run_capture(argv, log_dir, timeout_s=timeout_s)
    result = {"status": "timeout" if timed_out else EXIT_STATUS.get(code, "infrastructure_failed"),
              "code": "E_TIMEOUT" if timed_out else f"ARVIEW_EXIT_{code}", "exit_code": code,
              "fixed_scale": True, "poses": "default", "worn_sides": True, "checkpoint": export["checkpoint"],
              "portrait": True, "lens_evidence": True,
              "appearance_policy": AR_APPEARANCE_POLICY, "lens_env_intensity": LENS_ENV_INTENSITY,
              "glb_sha256": export["sha256"], "export_sha256": export["sha256"],
              "log": str(log_dir / "capture.log"), "images": [], "sheets": []}
    report_path = out / "report.json"
    if not report_path.is_file():
        if not timed_out and code in (0, 3):
            result.update(status="infrastructure_failed", code="E_REPORT", message="capture report missing")
        return result
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if report.get("schema") != "arview.capture/1" or report.get("input", {}).get("sha256") != export["sha256"]:
            raise ValueError("capture report does not describe the exported bytes")
        records = report.get("images", [])
        for rec in records:
            file = confined(out, rec["file"], areas=(".",))
            if not file.is_file() or sha256(file) != rec.get("sha256"):
                raise ValueError("capture image bytes differ from the report")
        worn = [rec for rec in records if rec.get("kind") == "worn_crop"]
        sheet = contact_sheet(out, worn + [rec for rec in records if rec.get("kind") != "worn_crop"])
        response = background_response(out, report)
        result.update(report=str(report_path), report_sha256=sha256(report_path), capture_status=report.get("status"),
                      warnings=report.get("warnings", []), errors=report.get("errors", []),
                      spec=report.get("spec"), runtime=report.get("runtime"), geometry=report.get("geometry"),
                      lens_background_response=response, summary=response_summary(response),
                      worn_views=[{**rec, "path": str(out / rec["file"])} for rec in worn])
        if sheet is not None:
            result["sheets"] = [str(sheet)]
            result["images"] = [{"path": str(sheet), "sha256": sha256(sheet), "view": "AR front test face, checker, standard poses and close worn sides",
                                 "kind": "arview_capture", "checkpoint": export["checkpoint"]}]
    except (OSError, ValueError, TypeError, KeyError) as error:
        result.update(status="infrastructure_failed", code="E_INTEGRITY", message=str(error), images=[])
    return result
