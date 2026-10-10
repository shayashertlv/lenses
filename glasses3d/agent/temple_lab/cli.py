"""Prepare/readiness/request/run entry points. No paid call without the existing exact approval guard."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import shutil

from PIL import Image

from glasses3d.agent import approvals as A
from glasses3d.agent.estimate import estimate, initial_context_tokens
from glasses3d.agent.loop import (RunSettings, RunSpec, ToolContract, instructions_sha256,
                                tool_registry_sha256, run_astra, contract_problems)
from glasses3d.agent.session_inputs import ReferenceInput
from .lab import Lab, read, sha, data_url, write_json_atomic
from .tools import registry

REPO = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
SOURCE_SCENE = REPO / "automation/data/blender_agent/studio/jobs/8c82d986e6c14d26b4b38f3807712ea0/agent/scene.blend"
BRIEF = ("Prada model with flat temples: isolated visual editing tool experiment. Interpret all six beige product photos, "
         "mark the physical evidence, propose your own facet layout, then choose and revise its depth. "
         "Separately labelled starting-model renders show the preserved original's flat temples. "
         "These renders describe the model to edit; the beige photos describe the target. No preferred solution is supplied. "
         "The output is an experiment for owner review, not an accepted model.\n")


def implementation():
    files = sorted(HERE.glob("*.py")) + [HERE / "instructions.txt", REPO / "glasses3d/g3d/worker.py"]
    # Bind all existing construction/worker/seeing code too; an approved request must not silently change implementation.
    for folder in (REPO / "glasses3d/g3d", REPO / "glasses3d/g3d_host", REPO / "glasses3d/agent"):
        files.extend(folder.rglob("*.py"))
    return {str(p.relative_to(REPO)): sha(p) for p in sorted(set(files))}


def prepare(root):
    root = Path(root).resolve()
    if root.exists():
        raise ValueError("use a new empty package directory")
    inputs = root / "inputs"; images = inputs / "images"; images.mkdir(parents=True)
    assets = []
    def copy(src, rel):
        dst = inputs / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        digest = sha(src)
        if sha(dst) != digest:
            raise ValueError("copy verification failed")
        assets.append({"path": rel, "sha256": digest})
    copy(SOURCE_SCENE, "scene.blend")
    photos = []
    manifest = read(REPO / "glasses3d/data/reference/prada/manifest.json")
    for name, row in manifest["files"].items():
        if not name.startswith("reference-"):
            continue
        src = REPO / "glasses3d/data/reference/prada" / name
        if sha(src) != row["sha256"]:
            raise ValueError("reference manifest mismatch")
        copy(src, f"images/{name}")
        photos.append({"id": Path(name).stem, "path": f"images/{name}", "view": row["label"], "colour": "beige"})
    (inputs / "brief.txt").write_text(BRIEF, encoding="utf-8")
    copy(HERE / "instructions.txt", "instructions.txt")
    write_json_atomic(inputs / "lab.json", {"schema": "g3d.temple_lab/1", "assets": assets, "photos": photos})
    write_json_atomic(root / "job.json", {"schema": "g3d.job/1", "name": "Prada flat temple isolated experiment",
                                         "implementation": implementation(), "scope": "isolated, not production"})
    lab = Lab(root)
    try:
        lab.start()
        renders = lab.render(["full", "side", "oblique", "other-side", "top", "end"], ["native", "clay"])
        if renders["status"] != "ok":
            raise ValueError(f"initial renders failed: {renders}")
        closeups = lab.render(["temple-side", "temple-grazing", "temple-top"], ["native", "clay"])
        if closeups["status"] != "ok":
            raise ValueError(f"initial temple close-ups failed: {closeups}")
        model_views = []
        for group, view, ps in ((renders, "full", "native"),
                                *((closeups, v, "clay") for v in ("temple-side", "temple-grazing", "temple-top"))):
            source = next(lab.state["images"][iid] for iid in group["image_ids"]
                          if lab.state["images"][iid]["view"] == view and lab.state["images"][iid]["pass_name"] == ps)
            iid = f"start-{view}"
            rel = f"model/{iid}.png"
            copy(root / source["path"], rel)
            row = {"id": iid, "path": rel, "view": view, "pass_name": ps,
                   "checkpoint": source["checkpoint"], "render_id": source["render_id"]}
            lab.register_image(iid, inputs / rel, kind="render",
                               **{k: row[k] for k in ("view", "pass_name", "checkpoint", "render_id")})
            model_views.append(row)
        lab.manifest.update(assets=assets, model_views=model_views)
        write_json_atomic(inputs / "lab.json", lab.manifest)
        lab.save()
        initial = lab.inspect()
        tools = registry(lab)
        write_json_atomic(root / "tools.json", [{"name": t.name, "description": t.description,
                                                 "parameters": t.params_json_schema} for t in tools])
        write_json_atomic(root / "prepared.json", {"status": "prepared", "initial": initial, "renders": renders,
                                                   "closeups": closeups, "model_views": model_views,
                                                   "exit_checkpoint": lab.exit_checkpoint(), "paid_calls": 0})
        if not lab.exit_checkpoint()["verified"]:
            raise ValueError("initial checkpoint verification failed")
    finally:
        lab.close()
    return {"status": "prepared", "path": str(root), "photos": len(photos),
            "starting_model_images": len(model_views), "paid_calls": 0}


def references(lab):
    out = []
    rows = [(row, f"TARGET PHOTO {row['id']}: {row['colour']} {row['view']} ({i}/{len(lab.manifest['photos'])})")
            for i, row in enumerate(lab.manifest["photos"], 1)]
    rows += [(row, f"STARTING MODEL {row['id']}: original flat temples, {row['pass_name']} render at {row['checkpoint']}")
             for row in lab.manifest.get("model_views", [])]
    for row, label in rows:
        p = lab.root / "inputs" / row["path"]
        with Image.open(p) as im:
            size = im.size
        out.append(ReferenceInput(label=label, path=row["path"],
                                   image_url=data_url(p), detail="high", view=row["view"], size_px=size))
    return out


def make_spec(root, run_id, settings, cap, *, write_request=False):
    lab = Lab(root)
    job = read(lab.root / "job.json")
    if job["implementation"] != implementation():
        raise ValueError("implementation changed since preparation; prepare a fresh package")
    lab.verify_inputs()
    if lab.state.get("candidates") or lab.state.get("marks") or lab.state.get("events"):
        raise ValueError("paid test requires a clean starting package, not a rehearsal copy")
    if lab.s.head and lab.s.head["fingerprint"] != lab.state.get("start_fingerprint"):
        raise ValueError("starting scene is no longer the flat original")
    tools = registry(lab)
    instructions = (lab.root / "inputs/instructions.txt").read_text(encoding="utf-8")
    brief = (lab.root / "inputs/brief.txt").read_text(encoding="utf-8")
    photos = references(lab)
    contract = ToolContract(frozenset(t.name for t in tools))
    problems = contract_problems(contract, instructions=instructions, brief=brief, tools=tools)
    if problems:
        raise ValueError(problems)
    request = A.build_request(lab.root, instructions_sha256=instructions_sha256(instructions),
                              tool_registry_sha256=tool_registry_sha256(tools), settings=settings.approval_settings(),
                              run_inputs=A.run_input_binding(lab.root, brief, photos))
    if write_request:
        chars = len(instructions)+len(brief)+len(json.dumps([t.params_json_schema for t in tools]))
        cost = estimate(initial_context_tokens(chars, len(photos)), settings.max_turns, settings.max_output_tokens)
        cost["limitation"] = "Historical frozen tariff/corpus forecast; many image tool outputs may increase cost. " \
                             "The monetary cap is enforced independently. No approval has been recorded."
        A.write_approval_request(lab.root / "runs" / run_id, job_id=lab.root.name, request=request,
                                 estimate=cost, suggested_cap_usd=cap)
    spec = RunSpec(job_dir=lab.root, run_id=run_id, instructions=instructions, tools=tools, brief=brief,
                   photos=photos, current_request_sha256=A.request_sha256(request), settings=settings,
                   requested_cap_usd=cap, scene_note="The scene is the original Prada with flat temples; call lab_inspect.",
                   contract=contract)
    return spec, lab


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=["prepare", "request", "run"])
    p.add_argument("--job", type=Path, required=True)
    p.add_argument("--run-id", default="isolated-test-001")
    p.add_argument("--effort", choices=["high", "max"])
    p.add_argument("--turns", type=int)
    p.add_argument("--cap")
    a = p.parse_args()
    if a.action == "prepare":
        result = prepare(a.job)
    else:
        if a.effort is None or a.turns is None or a.cap is None:
            p.error("request/run requires explicit --effort, --turns and --cap")
        spec, lab = make_spec(a.job, a.run_id, RunSettings(effort=a.effort, max_turns=a.turns), a.cap,
                              write_request=a.action == "request")
        result = {"status": "approval_request_ready", "request_sha256": spec.current_request_sha256} if \
                 a.action == "request" else asyncio.run(run_astra(spec, scene=lab))
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
