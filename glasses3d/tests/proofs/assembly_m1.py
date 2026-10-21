"""$0 assembly proof: actual Blender photos, new job, scripted SDK, fitted cameras, AR and delivery.

Run with the repository Python, as a module. All provider transports are mocked and non-loopback network is refused.
Artifacts and synthetic jobs are retained under data/assembly only. No product-specific runtime inputs are used.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import time
from uuid import uuid4

import numpy as np
from PIL import Image

from glasses3d.agent import approvals, cli, job_run, procs, replay
from glasses3d.agent.budget import RunLedger
from glasses3d.agent.job_scene import JobScene
from glasses3d.agent.offline import LoopbackOnly, ProductionTransportTripwire
from glasses3d.agent.receipts import BlobStore
from glasses3d.tests.test_cameras_fit import WRAP, bar, ring
from glasses3d.tests.test_fit_views_synthetic import lens, photo, TRUTH as FIT_TRUTH
from glasses3d.tests.proofs.offline_job_approval import prepare_offline_request

ROOT = Path(__file__).resolve().parents[2]
# Exercise a canonical-name collision and an ordinary filename with a space through
# job intake, camera fitting and the reference renderer without changing the cameras.
TRUTH = {"angle photo.png" if name == "angled.png" else name: values for name, values in FIT_TRUTH.items()}


def build_code():
    parts = []
    lens_outlines = []
    front = [ring(-32, 0, 26, 19, 4, 4), ring(32, 0, 26, 19, 4, 4),
             bar((-8, 9, 0), (8, 9, 0), 3, 4), bar((-60, 6, 0), (-66, 6, 0), 4, 8),
             bar((60, 6, 0), (66, 6, 0), 4, 8), bar((-12, -10, -3), (-11, -14, -6), 4, 3)]
    for i, (v, f) in enumerate(front):
        v = v.copy()
        v[:, 2] -= WRAP * v[:, 0] ** 2
        parts.append((f"Front_{i}", v, f, "frame"))
    for sx in (-1, 1):
        outline, _ = lens(sx * 32, 0, 23, 16)
        # Keep the camera fixture's exact ellipse, wrap and 1 mm thickness, but
        # sample the cap interior as a curved plate. A single triangle fan's face
        # centers lie on one ring and cannot define the seat check's local normals.
        p = outline[1:, :2]
        lens_outlines.append((f"Lens_{sx}", p.tolist(), p.mean(axis=0).tolist()))
        zh = -WRAP * 66 ** 2
        a = bar((sx * 66, 6, zh - 2), (sx * 69, 5, -110), 3, 6)
        b = bar((sx * 69, 5, -110), (sx * 68, -18, -138), 3, 5)
        parts.append((f"Temple_{sx}", np.vstack([a[0], b[0]]), np.vstack([a[1], b[1] + len(a[0])]), "temple"))
    packed = []
    for name, v, f, role in parts:
        v = v.copy()
        v[:, 1] -= 7.0  # bridge bottom is y=7 in the camera-test fixture
        packed.append((name, v.tolist(), f.tolist(), role))
    return """import bpy
for ob in list(bpy.data.objects):
    if ob.type == 'MESH':
        bpy.data.objects.remove(ob, do_unlink=True)
materials = {}
for role in ('frame', 'lens', 'temple'):
    mat = bpy.data.materials.new(role)
    mat.diffuse_color = (0.08, 0.045, 0.025, 1.0)
    mat.use_nodes = True
    node = mat.node_tree.nodes.get('Principled BSDF')
    node.inputs['Base Color'].default_value = mat.diffuse_color
    node.inputs['Roughness'].default_value = 0.5
    materials[role] = mat
parts = """ + repr(packed) + """
# The default ten-ring topology of tests.fixtures.seat_fixtures.lens, generated
# here to keep the proof inside the ordinary execute_blender_code size limit.
for name, outline, center in """ + repr(lens_outlines) + """:
    n = len(outline)
    front = []
    for scale in """ + repr(np.linspace(1.0, 0.1, 10).tolist()) + """:
        for x, y in outline:
            front.append((center[0] + (x - center[0]) * scale,
                          center[1] + (y - center[1]) * scale, 0.5))
    front.append((center[0], center[1], 0.5))
    nv = len(front)
    vertices = front + [(x, y, z - 1.0) for x, y, z in front]
    vertices = [(x, y - 7.0, z - """ + repr(WRAP) + """ * x ** 2) for x, y, z in vertices]
    front_faces = []
    for r in range(9):
        for i in range(n):
            a0, a1 = r * n + i, r * n + (i + 1) % n
            b0, b1 = (r + 1) * n + i, (r + 1) * n + (i + 1) % n
            front_faces.extend(((a0, a1, b1), (a0, b1, b0)))
    for i in range(n):
        front_faces.append((9 * n + i, 9 * n + (i + 1) % n, nv - 1))
    faces = front_faces + [(c + nv, b + nv, a + nv) for a, b, c in front_faces]
    for i in range(n):
        j = (i + 1) % n
        faces.extend(((i, i + nv, j + nv), (i, j + nv, j)))
    parts.append((name, vertices, faces, 'lens'))
for name, vertices, faces, role in parts:
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    ob = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(ob)
    ob['partRole'] = role
    mesh.materials.append(materials[role])
"""


def check_receipts(job: Path, run_id: str, expected_tools: list[str]) -> dict:
    """Read-only independent audit, also usable against a retained run without repeating Blender or camera fits."""
    job = Path(job).resolve()
    run_dir = job / "runs" / run_id

    def read(path):
        return json.loads(Path(path).read_text(encoding="utf-8"))

    def local(path):
        path = (job / path).resolve()
        assert path.is_relative_to(job) and path.is_file(), path
        return path

    n = len(expected_tools)
    expected_transport = [f"{i:04d}-{part}.json" for i in range(1, n + 1) for part in ("request", "response")]
    assert sorted(p.name for p in (run_dir / "transport").iterdir()) == expected_transport
    result = read(run_dir / "result.json")
    assert result["requests"] == result["turns_used"] == result["tool_calls"] == n
    assert result["error"] is None and not result["teardown_errors"] and not result["transcript_errors"]
    ledger = RunLedger.load(run_dir / "budget.json")  # re-prices every row and verifies its durable history
    assert len(ledger.rows) == n and ledger.violation is None and ledger.blocked is None
    assert [row["sequence"] for row in ledger.rows] == list(range(1, n + 1))
    settled = 0
    for row in ledger.rows:
        assert row["outcome"] == "settled" and row["settled_from"] == "transport" and row["http_status"] == 200
        assert settled + row["reserved_micro_usd"] <= ledger.maximum_micro
        response = read(run_dir / "transport" / f"{row['sequence']:04d}-response.json")
        assert response["id"] == row["response_id"] and response["usage"]["input_tokens"] == row["input_tokens"]
        settled += row["settled_micro_usd"]
    assert result["budget"]["settled_micro_usd"] == settled
    assert result["budget"]["unknown_micro_usd"] == result["budget"]["held_micro_usd"] == 0
    job_ledger = read(job / "budget/job-ledger.json")
    assert not job_ledger["blocking"]
    assert job_ledger["totals"]["requests"] == n and job_ledger["totals"]["settled_micro_usd"] == settled
    assert job_ledger["totals"]["unknown_micro_usd"] == job_ledger["totals"]["held_micro_usd"] == 0

    state = read(job / "work/job_scene/state.json")
    events = [json.loads(line) for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    for kind in ("api_request", "api_response", "tool_started", "tool_finished"):
        assert sum(event["event"] == kind for event in events) == n, kind
    expected_ids = [f"call_{i:04d}" for i in range(1, n + 1)]
    journal = {event["call_id"]: event for event in state["events"] if event.get("kind", "call") == "call"}
    assert sorted(journal) == expected_ids
    folders = sorted((run_dir / "tools").iterdir())
    assert [folder.name for folder in folders] == [f"{i:04d}-{name}" for i, name in enumerate(expected_tools, 1)]
    expected_status = {"export_glb": "exported", "finish": "accepted"}
    blobs = BlobStore(job / "blobs")
    image_digests = set()

    def verify_blobs(value):
        if isinstance(value, dict):
            if all(key in value for key in ("sha256", "bytes", "mime")) and str(value["mime"]).startswith("image/"):
                blob = blobs.path(value["sha256"], value["mime"])
                assert blob.stat().st_size == value["bytes"] and approvals.file_sha256(blob) == value["sha256"]
                image_digests.add(value["sha256"])
            for child in value.values():
                verify_blobs(child)
        elif isinstance(value, list):
            for child in value:
                verify_blobs(child)

    last_checkpoint = None
    for i, (folder, name, cid) in enumerate(zip(folders, expected_tools, expected_ids), 1):
        args, rec, jr = [read(folder / filename) for filename in ("arguments.json", "result.json", "journal.json")]
        assert args["call_id"] == rec["call_id"] == jr["call_id"] == cid
        assert args["tool"] == rec["tool"] == jr["tool"] == journal[cid]["tool"] == name
        assert isinstance(rec["result"], list) and rec["result"][0]["type"] == "text"
        output = json.loads(rec["result"][0]["text"])
        status = ("review_required" if name == "finish" and args["arguments"].get("review_call_id") is None else
                  "refused" if name == "finish" and i < n else expected_status.get(name, "ok"))
        assert output["status"] == jr["status"] == journal[cid]["status"] == status, (name, output)
        if name == "render_views":
            rendered = read(local(output["manifest"]))
            views = list(rendered["views"].values())
            assert len(views) == len(TRUTH) and all(view["kind"] == "reference" for view in views)
            assert {view["ref"] for view in views} == {Path(photo).stem for photo in TRUTH}
            for view in views:
                basis = read(job / "cameras" / view["basis"] / "basis.json")
                assert view["scale_k"] == 1 and view["camera"] == basis["refs"][view["ref"]]["camera"]
        assert jr["mutating"] == (jr["checkpoint_before"] != jr["checkpoint_after"])
        if last_checkpoint is not None:
            assert jr["checkpoint_before"] == last_checkpoint
        last_checkpoint = jr["checkpoint_after"]
        assert jr["mutating"] == (name in {"execute_blender_code", "seat_lens"})
        if name == "check_lens_seating":
            assert output["result"]["ok"], output["result"]
        response = read(run_dir / "transport" / f"{i:04d}-response.json")
        calls = [item for item in response["output"] if item.get("type") == "function_call"]
        assert [(item["call_id"], item["name"]) for item in calls] == [(cid, name)]
        verify_blobs(rec)
    assert last_checkpoint == result["exit_checkpoint"]["id"]
    for path in (run_dir / "transport").iterdir():
        verify_blobs(read(path))
    assert image_digests, "the photos and tool images must have durable blob receipts"
    for rec in state["images"]:
        assert approvals.file_sha256(local(rec["path"])) == rec["sha256"]
        assert rec["sha256"] in image_digests

    report = read(job / "deliverable/report.json")
    ar = report["arview"]
    ar_path = local(ar["report"])
    assert approvals.file_sha256(ar_path) == ar["report_sha256"]
    capture = read(ar_path)
    assert ar["exit_code"] == 0 and capture["request"]["fixed_scale"] is True
    assert ar["export_sha256"] == capture["input"]["sha256"] == report["glb_sha256"]
    assert capture["images"], "AR must produce actual images"
    for rec in capture["images"]:
        path = (ar_path.parent / rec["file"]).resolve()
        assert path.is_relative_to(ar_path.parent) and approvals.file_sha256(path) == rec["sha256"]
    for sheet in ar["sheets"]:
        assert local(sheet).is_file()
    comparison = report["look_comparison"]
    assert comparison["status"] == "ok" and comparison["informational_only"]
    assert {row["ref"] for row in comparison["views"]} == {Path(name).stem for name in TRUTH}
    assert comparison["views"][0]["view_kind"] == "front"
    assert comparison["export_sha256"] == report["glb_sha256"]
    for asset in comparison["assets"]:
        assert approvals.file_sha256(local(asset["path"])) == asset["sha256"]
    worn = comparison["worn_capture"]
    assert approvals.file_sha256(local(worn["report"])) == worn["report_sha256"]
    assert read(local(worn["report"]))["input"]["sha256"] == report["glb_sha256"]
    assert report["finish_images"]

    for path in job.rglob("*"):
        if path.is_file():
            assert not path.name.startswith(".env"), "an environment file must not be a job artifact"
            payload = path.read_bytes()
            assert b"data:image" not in payload, f"raw image URL persisted in {path.relative_to(job)}"
            assert b"sk-offline-job-proof" not in payload, f"offline API key persisted in {path.relative_to(job)}"
    return {"transport_files": 2 * n, "successful_tools": n, "settled_requests": n,
            "simulated_settled_micro_usd": settled, "verified_image_blobs": len(image_digests),
            "verified_ar_images": len(capture["images"]), "receipts_clean": True}


def check_decisions(job: Path, run_id: str, difference_id: str):
    """Audit the refusal and resolution, following the hash-bound full report when paged."""
    run_dir = job / "runs" / run_id
    refused = json.loads((run_dir / "tools/0014-finish/result.json").read_text(encoding="utf-8"))
    output = json.loads(refused["result"][0]["text"])
    assert output["status"] == "refused" and output["code"] == "E_DIFFERENCES", output
    if output.get("paged"):
        receipt = output["full_report"]
        path = (job / receipt["path"]).resolve()
        assert path.is_relative_to(job.resolve()) and approvals.file_sha256(path) == receipt["sha256"]
        output = json.loads(path.read_text(encoding="utf-8"))["result"]
    assert output["open_difference_ids"] == [difference_id], output
    decisions = [json.loads(line) for line in (run_dir / "differences.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [row["item"]["status"] for row in decisions if row["kind"] == "difference"] == ["open", "limitation"]


def run():
    started = time.monotonic()
    token = uuid4().hex[:8]
    work = ROOT / "data" / "assembly" / ("m1-proof-" + token)
    work.mkdir(parents=True)
    code = build_code()
    truth = JobScene(work / "truth", job={})
    try:
        truth.start()
        built = truth.s.execute(code, label="synthetic truth")
        assert built["status"] == "ok", built
        export = truth.seeing.export_head()
        assert export["validate_spec"]["ok"], export
        truth_views = {name: f"truth-{i:02d}" for i, name in enumerate(TRUTH, 1)}
        views = [{"name": truth_views[name], "yaw": yaw, "pitch": pitch, "roll": roll,
                  "perspective": perspective, "width_mm": 640 / 3}
                 for name, (_, yaw, pitch, roll, perspective) in TRUTH.items()]
        rendered = truth.seeing.render_views(views, passes=["silhouette"], size_px=640, background="white")
        assert rendered["status"] == "ok", rendered
        photos = work / "photos"
        photos.mkdir()
        for name in TRUTH:
            source = rendered["views"][truth_views[name]]["files"]["silhouette"]
            with Image.open(source) as image:
                # The existing known-truth fit fixture colours a hard silhouette. Use the
                # Blender-rendered silhouette here too: faint AA fringes are not extra geometry.
                Image.fromarray(photo(np.asarray(image) > 127)).save(photos / name)
    finally:
        truth.close()
    viewfile = work / "views.json"
    viewfile.write_text(json.dumps({name: {"kind": values[0], "side": "left" if name == "side.png" else None}
                                   for name, values in TRUTH.items()}), encoding="utf-8")
    created = cli.dispatch(cli.parser().parse_args([
        "job", "--jobs-root", str(work / "jobs"), "create", "--images", str(photos), "--views", str(viewfile), "--name", "Synthetic assembly proof",
        "--description", "Procedural wrapped elliptical frame, dark lenses, two temples and one nose pad."]))
    job = Path(created["path"])
    assert job.resolve().is_relative_to((work / "jobs").resolve()), job
    replies = [replay.tool_call("execute_blender_code", {"code": code, "soft_timeout_s": 120, "label": "build"}),
               replay.tool_call("scene_summary", {"objects": None}),
               replay.tool_call("fit_cameras", {"reason": "Fit the rebuilt truth geometry to all rendered photos", "refs": None, "workers": None}),
               replay.tool_call("render_views", {"views": ["reference"], "passes": ["clay"], "size_px": 640,
                                                "background": "white", "basis": None}),
               *[replay.tool_call("seat_lens", {"lens": f"Lens_{sx}", "frame_type": "acetate",
                                               "frame_parts": None, "lower_edge": None, "capture_mm": None,
                                               "dry_run": False}) for sx in (-1, 1)],
               replay.tool_call("check_lens_seating", {"lenses": None, "frame_parts": None, "frame_type": None,
                                                      "hidden_x_range_mm": None, "lower_edge": None,
                                                      "stations_table": False}),
               replay.tool_call("compare_to_photos", {"refs": None, "parts": "all", "size_px": 640, "basis": None}),
               replay.tool_call("export_glb"), replay.tool_call("arview_capture"),
               replay.tool_call("compare_look_to_photos", {"refs": None, "basis": None})]
    difference = {"id": "synthetic-colour", "what_differs": "The synthetic reference colours encode silhouettes, not the authored material.",
                  "evidence_ref": "call_0011", "status": "open", "last_correction": "No material correction tried.",
                  "outcome": "Shape has been compared, but the synthetic palette is not a product appearance target.",
                  "next_action": "Review the current comparison and classify the synthetic colour limitation.",
                  "refs": [Path(name).stem for name in TRUTH], "reason": None}
    photo_rows = [{"ref": Path(name).stem, "differences": difference["what_differs"],
                   "difference_ids": [difference["id"]]} for name in TRUTH]
    replies += [
        replay.tool_call("record_difference", difference),
        replay.tool_call("finish", {"summary": "Synthetic offline frame and lenses, cameras fitted, compared, validated and captured in AR.",
                                    "per_photo": photo_rows}),
        replay.tool_call("finish", {"summary": "Attempting finish with the recorded open synthetic colour difference.",
                                    "review_call_id": "call_0013", "per_photo": photo_rows}),
        replay.tool_call("record_difference", {**difference, "status": "limitation", "evidence_ref": "call_0013",
            "reason": "These procedural photos deliberately encode a hard silhouette; their false colour cannot validate material likeness.",
            "outcome": "The delivered current comparison shows the silhouette-coded reference beside the authored material.",
            "next_action": "No material correction justified by synthetic false colour; owner appearance remains untested."}),
        replay.tool_call("finish", {"summary": "Scripted differences reconciliation passed; no automated likeness judgment.",
                                    "review_call_id": "call_0013", "per_photo": photo_rows})]
    transport = replay.ScriptedOpenAI(replies)
    settings = replace(job_run.settings_default(), max_turns=len(replies) + 1)
    spec, _ = prepare_offline_request(job, "offline-proof", transport.handle, settings=settings)
    outcome = asyncio.run(job_run.execute_run(job, spec.run_id, offline_handler=transport.handle))
    procs.write_json_atomic(work / "outcome.json", outcome)
    assert outcome["result"]["status"] == "completed", outcome
    assert outcome["delivery"]["status"] == "delivered", outcome
    assert not transport.violations and not transport.unexpected, transport.violations
    assert len(transport.counts) == len(transport.creates) == outcome["result"]["requests"] == len(replies)
    receipt_checks = check_receipts(job, spec.run_id, [
        "execute_blender_code", "scene_summary", "fit_cameras", "render_views", "seat_lens", "seat_lens",
        "check_lens_seating", "compare_to_photos", "export_glb",
        "arview_capture", "compare_look_to_photos", "record_difference", "finish", "finish", "record_difference", "finish"])
    check_decisions(job, spec.run_id, difference["id"])
    basis = json.loads(next((job / "cameras").rglob("cameras.json")).read_text())
    for name, rec in basis["images"].items():
        _, yaw, pitch, _, _ = TRUTH[name]
        assert rec["iou"] > .97 and rec["contour_mean_px"] < 1.5, (name, rec)
        assert abs(rec["camera"]["yaw"] - yaw) < 1.5 and abs(rec["camera"]["pitch"] - pitch) < 1.5, (name, rec)
    report = json.loads((job / "deliverable/report.json").read_text())
    assert report["glb_sha256"] == approvals.file_sha256(job / "deliverable/model.glb")
    assert report["validate_spec"]["ok"] and report["seating"]["ok"] and report["arview"]["exit_code"] == 0
    assert outcome["result"]["exit_checkpoint"]["verified"]
    elapsed = time.monotonic() - started
    assert elapsed < 900, elapsed
    receipt = {"status": "passed", "job": str(job), "seconds": elapsed, "requests": len(replies),
               "actual_spend_usd": 0, "report": str(job / "deliverable/report.json"), "receipt_checks": receipt_checks}
    procs.write_json_atomic(work / "proof.json", receipt)
    print(json.dumps(receipt, indent=2), flush=True)
    return receipt


if __name__ == "__main__":
    os.environ["G3D_OFFLINE"] = "1"
    for key in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_ORG_ID", "OPENAI_PROJECT_ID", "OPENAI_ADMIN_KEY",
                "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        os.environ.pop(key, None)
    with LoopbackOnly() as network, ProductionTransportTripwire() as clients:
        run()
    assert not network.attempts and not clients.constructions
