"""The mutating worker self-test (doctor --self-test): fixed fixtures and negative controls in the isolated worker.

Runs only when the Docker doctor finds the engine and the pinned image. Every check writes into the fresh output
folder given on the command line; the doctor record is written into a COPY of the worker config there, never over the
owner's file, and it is bound to the config/image/harness fingerprint so any change invalidates it.

The fixture renders one small view and prints one lens mark in a worker font (review 2026-09-28): its check fails
without the worker's GL renderer record (the render ran, and on what), on a font fallback note (the image's fonts are
missing), without the render, or when the font file used is not in the image's font inventory. The doctor record carries
the renderer and the font file, and passed_utc is UTC (executor.utc_stamp).
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import time

from ..candidates import MODULE_ORDER
from .executor import DockerWorker, Worker, WorkerError, WorkerRefused, utc_stamp, worker_config_fingerprint, write_bundle

FIXTURE_FRAME = '''
W, Hh = 140.0, 50.0
outer = gl.rounded_rect(W, Hh, 10.0, n=160)
lensR = gl.rounded_rect(52.0, 38.0, 9.0, n=96, center=(32.0, -2.0))
lensL = gl.rounded_rect(52.0, 38.0, 9.0, n=96, center=(-32.0, -2.0))
front = gl.plate_with_holes(outer, [lensR, lensL], z_front=0.0, thickness=6.0, name="front_plate", part="frame", component="front")
acetate = gl.material_acetate("acetate_navy", (28, 40, 70))
gl.assign(front, acetate)
optics = gl.lens_optics(transmission_top_rgb=(0.25, 0.20, 0.14), transmission_bottom_rgb=(0.75, 0.70, 0.62))
lens_mat = gl.material_lens("lens", optics)
lenses = {}
for side, outline in (("R", lensR), ("L", lensL)):
    lens = gl.lens_solid(gl.offset_closed(outline, 0.8), z_front=-1.5, name=f"lens_{side}", part=f"lens_{side}", base_curve=4.0, thickness=2.0)
    gl.assign(lens, lens_mat)
    lenses[side] = lens
# one lens mark in a worker font: the build notes name the font file used, or say Blender's default was the last resort
mark = gl.lens_print(lenses["R"], "LENSES", 4.0, (32.0, 8.0), "selftest_mark", font='sans', component="mark")
gl.assign(mark, acetate)
for side, sx in (("R", 1.0), ("L", -1.0)):
    secs = []
    for k, z in enumerate(np.linspace(-3.0, -152.0, 12)):
        secs.append(gl.section_rect((sx * (W / 2 - 3.0), 0.0, z), 6.0, 8.0, (1, 0, 0), (0, 1, 0), radius=1.5, n=24))
    t = gl.loft(secs, f"temple_{side}", f"temple_{side}", "arm")
    gl.assign(t, acetate)
gl.set_bridge_underside((0.0, -7.0, -3.0))
gl.note("self-test fixture built")
'''
FIXTURE_FONT = "sans"          # a style the image's DejaVu / Liberation fonts serve ('script' and 'handwritten' have no face there)
# one small textured view: the self-test proves the render context and records its renderer, not image quality
FIXTURE_RENDERS = [{"id": "selftest_front", "kind": "textured", "width": 160, "height": 120, "transparent": True,
                    "camera": {"type": "orbit", "yaw": 0, "pitch": 0, "roll": 0, "ortho": True, "px_per_mm": "fit", "target": "bbox", "distance_mm": 600}}]
# glasses_lib.load_font's fallback note: "font 'sans' not found ([...]); Blender's default font is used as the last resort"
FONT_FALLBACK = re.compile(r"^font .* not found|last resort", re.IGNORECASE)
# negative controls: each must FAIL inside the worker (no network, no host read, no write outside the tmpfs)
CONTROL_NETWORK = "import socket\nsocket.create_connection(('1.1.1.1', 53), timeout=5)\ngl.note('NETWORK REACHED')\n"
CONTROL_HOST_READ = "open('/bundle/../etc/hostname').read()\nimport os\nos.listdir('/host')\ngl.note('HOST READ')\n"
CONTROL_WRITE_OUTSIDE = "open('/bundle/escape.txt', 'w').write('x')\ngl.note('WROTE OUTSIDE')\n"
CONTROL_SENTINEL = "open('/etc/lenses-sentinel').read()\ngl.note('SENTINEL READ')\n"


def fixture_problems(outcome, result: dict | None, files: list[dict]) -> tuple[list[str], str | None]:
    """What the fixture build fails to prove (empty: everything), and the font file its text used.

    The worker's GL renderer record (executor.WorkerOutcome.gl, from worker/entry.py's probe at the first render) must name
    a renderer without an error; no build note may be a font fallback; a note must name the file FIXTURE_FONT resolved to,
    and that file must be in the image's font inventory when the image carries one (older images do not); every
    FIXTURE_RENDERS view must come back without an error and be among the ingested files."""
    problems = []
    gl = getattr(outcome, "gl", None) or {}
    if not str(gl.get("renderer") or "").strip():
        problems.append("no GL renderer record: the worker's probe did not report the render context (outcome.gl)")
    elif gl.get("error"):
        problems.append(f"GL probe error: {str(gl['error'])[:200]}")
    notes = [str(n) for n in ((result or {}).get("notes") or [])]
    fallback = [n for n in notes if FONT_FALLBACK.search(n)]
    if fallback:
        problems.append(f"font fallback: {fallback[0][:300]}")
    prefix = f"font {FIXTURE_FONT!r}: "
    used = next((n[len(prefix):].strip() for n in notes if n.startswith(prefix)), None)
    if used is None:
        problems.append(f"no note names the font file {FIXTURE_FONT!r} resolved to: the lens mark was not built")
    else:
        inventory = getattr(outcome, "fonts", None)
        if inventory and used not in inventory:
            problems.append(f"the font file used ({used}) is not in the image's font inventory ({len(inventory)} files)")
    have = {Path(f["rel"]).name for f in files or []}
    rows = {r.get("id"): r for r in ((result or {}).get("renders") or []) if isinstance(r, dict)}
    for spec in FIXTURE_RENDERS:
        row = rows.get(spec["id"])
        if row is None:
            problems.append(f"render {spec['id']} missing from the result")
        elif row.get("error"):
            problems.append(f"render {spec['id']} failed: {str(row['error'])[:200]}")
        elif f"{spec['id']}.png" not in have:
            problems.append(f"render {spec['id']} was not among the ingested files")
    return problems, used


def run_check(worker: Worker, out: Path, name: str, program: str, *, expect_ok: bool, forbidden_note: str | None = None,
              renders: list[dict] | None = None, fixture: bool = False) -> dict:
    """One program through ``worker`` into ``out/name`` (bundle, work, staging). ``fixture``: the build must also pass
    ``fixture_problems``."""
    op_dir = Path(out) / name
    bundle = op_dir / "bundle"
    work = op_dir / "work"
    staging = op_dir / "staging"
    for d in (bundle, work, staging):
        d.mkdir(parents=True, exist_ok=True)
    modules = {"frame": program}
    operation = write_bundle(bundle, operation_id=name, mode="build", modules=modules, module_order=list(MODULE_ORDER), renders=list(renders or []),
                             evidence=None, blend_bytes=None, time_limit_s=240)
    identity = {"job_short": "selftest", "operation_id": name, "attempt": 1}
    t0 = time.monotonic()
    try:
        identity = worker.launch(bundle, operation, work_dir=work, deadline_s=240, identity=identity)
        outcome = worker.await_outcome(identity, deadline_s=240)
        ingested = worker.ingest(identity, staging) if outcome.status in ("completed", "failed") else None
    except (WorkerError, WorkerRefused) as e:
        return {"name": name, "passed": False, "error": str(e), "seconds": round(time.monotonic() - t0, 1)}
    result = ingested.result if ingested else None
    ok = bool(result and result.get("ok")) and outcome.status == "completed"
    notes = (result or {}).get("notes") or []
    passed = (ok == expect_ok) and (forbidden_note is None or forbidden_note not in notes)
    check = {"name": name, "passed": passed, "worker_ok": ok, "outcome": outcome.status, "notes": notes,
             "ingest_problems": ingested.problems if ingested else None, "files": len(ingested.files) if ingested else 0,
             "seconds": round(time.monotonic() - t0, 1)}
    if fixture:
        problems, used = fixture_problems(outcome, result, ingested.files if ingested else [])
        check.update(problems=problems, gl=getattr(outcome, "gl", None), font_used=used, passed=passed and not problems)
    return check


def run_self_test(cfg: dict, out: Path, *, worker: Worker | None = None) -> dict:
    """``worker``: the Docker worker of ``cfg`` unless given (tests drive the same checks through a stub or the native
    fixture worker)."""
    out = Path(out)
    if worker is None:
        worker = DockerWorker(dict(cfg, doctor={"passed_utc": "self-test", "fingerprint": worker_config_fingerprint(cfg)}))
    report = {"passed": False, "checks": [], "fingerprint": worker_config_fingerprint(cfg)}

    def run(name: str, program: str, **kw) -> dict:
        return run_check(worker, out, name, program, **kw)

    report["checks"].append(run("fixture-build", FIXTURE_FRAME, expect_ok=True, renders=FIXTURE_RENDERS, fixture=True))
    report["checks"].append(run("control-network", CONTROL_NETWORK, expect_ok=False, forbidden_note="NETWORK REACHED"))
    report["checks"].append(run("control-host-read", CONTROL_HOST_READ, expect_ok=False, forbidden_note="HOST READ"))
    report["checks"].append(run("control-write-outside", CONTROL_WRITE_OUTSIDE, expect_ok=False, forbidden_note="WROTE OUTSIDE"))
    report["checks"].append(run("control-sentinel", CONTROL_SENTINEL, expect_ok=False, forbidden_note="SENTINEL READ"))
    report["passed"] = all(c.get("passed") for c in report["checks"])
    # the fixture's exported arrays must produce a contract-valid GLB and load in the actual AR runtime (host, trusted);
    # a fixture that exported nothing fails here (it used to skip this stage and pass)
    fixture = out / "fixture-build" / "staging"
    if report["passed"] and not (fixture / "parts.npz").is_file():
        report["export"] = {"error": "the fixture build produced no parts.npz: nothing to export or load in the AR runtime"}
        report["passed"] = False
    elif report["passed"]:
        try:
            from .. import export as mexport
            rec = mexport.export_glb(fixture / "parts.npz", fixture / "materials.json", out / "fixture.glb", extras={"selftest": True})
            report["export"] = {"contract_ok": rec["contract"]["ok"], "failures": rec["contract"].get("failures"), "sha256": rec["sha256"]}
            from bsa import archeck
            res = archeck.run({"fixture": out / "fixture.glb"}, out / "ar", width_mm={"fixture": 140.0})
            val = archeck.validate_ar_result(res, expected_models={"fixture": rec["sha256"]}, expected_views=["front", "angled"])
            report["ar"] = {"valid": bool(val["ok"]), "reasons": val.get("reasons")}
            report["passed"] = report["passed"] and bool(rec["contract"]["ok"]) and bool(val["ok"])
        except Exception as e:  # noqa: BLE001
            report["export"] = {"error": f"{type(e).__name__}: {e}"}
            report["passed"] = False
    if report["passed"]:
        built = report["checks"][0]
        stamped = dict(cfg, doctor={"passed_utc": utc_stamp(), "fingerprint": report["fingerprint"], "checks": [c["name"] for c in report["checks"]],
                                    "gl_renderer": (built.get("gl") or {}).get("renderer"), "font_used": built.get("font_used")})
        (out / "worker-config.passed.json").write_text(json.dumps(stamped, indent=1), encoding="utf-8")
        report["worker_config_with_doctor_record"] = str(out / "worker-config.passed.json")
    return report
