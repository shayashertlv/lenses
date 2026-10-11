"""The facet lab's package and run entry points (plan/WP6_FACETS.md §4.3-4.4, §5). No paid call without the exact
approval guard of agent/loop.py; prepare, estimate and request are $0.

    prepare  --job DIR [--rig photo_env]
        a new isolated package: inputs/ = a byte copy of the original scene, the target photos (checked against the
        reference manifest and the pose basis), brief.txt, instructions.txt and lab.json (g3d.temple_lab/2: the rig,
        every input's sha256, the photo list, the pose basis); basis/cameras.json = the pose basis restricted to the
        target photos, imported as cameras/basis-NN (outside inputs/: every image under inputs/ is sent in the first
        message); then Lab.prepare(): the original compared at every photo (with photo_env this fits and freezes the
        job's light), revision 0 = template_from_mesh of the original's +X temple, applied and compared. job.json
        binds the implementation (every .py of g3d, g3d_host and agent, and instructions.txt) and the frozen light.
    estimate [--rehearsal FILE] [--out FILE]
        the facet-run cost table from the T5 rehearsal's real tool outputs (tests/proofs/astra_temple_lab.py).
    request  --job DIR --run-id ID --effort high|max (--turns N | --no-turn-cap) --cap USD [--k 4]
        writes runs/<ID>/approval-request.json for a clean package (nothing approved, nothing sent).
    run      the same arguments [--env-file FILE]: the paid run, refused without the owner's approval of exactly that
        request.

The run settings are tools.run_settings: tool_choice required (kept after every call), final only on an accepted
facet_finish (K = --k distinct compared heads), the context guard at 240k counted input tokens.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
from pathlib import Path
import shutil
import sys

from PIL import Image

from glasses3d.agent import approvals as A
from glasses3d.agent.estimate import STUDIO_PARAMETERS, cents_text, suggested_cap_micro
from glasses3d.agent.loop import (RunRefused, RunSpec, ToolContract, contract_problems, instructions_sha256,
                                  run_astra, tool_registry_sha256)
from glasses3d.agent.pricing import LONG_CONTEXT_INPUT_TOKENS, Tariff, usd_text
from glasses3d.agent.seeing import ENV_RIG, MATCHED_RIGS
from glasses3d.agent.session_inputs import ReferenceInput
from glasses3d.g3d_host import compare as C
from .lab import LAB_SCHEMA, Lab, data_url, read, sha, write_json_atomic
from .tools import CONTEXT_GUARD_TOKENS, TOOL_NAMES, registry, run_settings

REPO = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
G3D = REPO / "glasses3d"
SOURCE_SCENE = REPO / "automation/data/blender_agent/studio/jobs/8c82d986e6c14d26b4b38f3807712ea0/agent/scene.blend"
SOURCE_SCENE_SHA256 = "56274debb901fb28dea1f93f0a6b6ae8401501f7145573f8dedb68c7d2436c06"
REFERENCE = G3D / "data/reference/prada"
BASIS_JSON = G3D / "data/wp6v2/basis/cameras.json"
LAB_DATA = G3D / "data/wp6v2/lab"
ESTIMATE = LAB_DATA / "estimate.json"
DEFAULT_RIG = ENV_RIG
K_TOKEN = "{K}"                     # instructions.txt states finish precondition 1 with the run's K

# The estimate (WP6_FACETS §4.3): tokens ~ 0.42 x text chars + per image 0.975 x ceil(W/32) x ceil(H/32) + 112, fitted
# on the 34 transport deltas of the v1 lab run owner-test-001 (median absolute residual 197 tokens). On that run's
# first request (19,726 text chars, 10 images) the formula gives 19,245 tokens against 15,121 counted: the brief part
# is over-estimated by 27 %, which is kept (conservative).
TOKENS_PER_CHAR, TOKENS_PER_PATCH, TOKENS_PER_IMAGE, PATCH_PX = 0.42, 0.975, 112, 32
V1_FIRST_REQUEST = {"formula_tokens": 19245, "counted_tokens": 15121}
# Output tokens per request (reasoning included). Expected: the v1 lab run (gpt-6-astra, effort max, 35 requests,
# 20,276 output tokens). High end: the Studio corpus median (estimate.STUDIO_PARAMETERS, 13 runs, all effort max).
# No gpt-6-astra ledger at effort high exists: high is priced with the max numbers, an upper bound.
OUTPUT_TOKENS = {"expected": 580, "high": math.ceil(STUDIO_PARAMETERS.output)}
# Tool calls per 10 requests. typical: the loop the instructions describe (edit and compare, now and then variants,
# a closer look, an inspect); edit_heavy: every request an applied facet_edit with its default compare.
MIXES = {"typical": {"facet_edit": 5, "facet_variants": 1, "photo_compare": 2, "photo_image": 1, "facet_inspect": 1},
         "edit_heavy": {"facet_edit": 10}}
STUBBED_KINDS = ("compare", "variants")         # the image kinds the context guard may replace (lab.context_stubs)
STUB_CHARS = 120                                 # one stub text
GUARD_KEEP_RESULTS = 3                           # the latest 2 compare / variant results and best_checkpoint's
RUN_LENGTHS = (40, 60, 90)
ESTIMATE_SCHEMA = "g3d.facet_estimate/1"


# --------------------------------------------------------------------------------------------- package
def implementation():
    files = sorted(HERE.glob("*.py")) + [HERE / "instructions.txt", REPO / "glasses3d/g3d/worker.py"]
    # Bind all construction/worker/seeing code too; an approved request must not silently change implementation.
    for folder in (REPO / "glasses3d/g3d", REPO / "glasses3d/g3d_host", REPO / "glasses3d/agent"):
        files.extend(folder.rglob("*.py"))
    return {str(p.relative_to(REPO)): sha(p) for p in sorted(set(files))}


def side_note(rec: dict) -> str:
    """Which temple a side photo shows: yaw > 0 looks from +X (g3d_host/camera.py), the charted temple."""
    yaw = float((rec.get("camera") or {}).get("yaw", 0.0))
    return "it shows the charted temple" if yaw > 0 else "it shows the mirrored temple, from its own side"


def brief_text(product: str, photos: list) -> str:
    lines = [f"Product: {product}, target colourway. The product photos follow; every one shows the target.", ""]
    for p in photos:
        lines.append(f"- {p['id']}: {p['view']}" + (f"; {p['note']}" if p.get("note") else ""))
    lines += ["", "The scene holds revision 0, a template fitted to the starting model's own temples. Make the temples "
                  "look like the temples in these photos."]
    return "\n".join(lines) + "\n"


def prepare(root, *, rig: str = DEFAULT_RIG, source_scene=SOURCE_SCENE, scene_sha256=SOURCE_SCENE_SHA256,
            reference=REFERENCE, basis_json=BASIS_JSON, lab_class=Lab, import_basis=C.import_cameras_json) -> dict:
    """A new package (module doc). $0; ~4 min with photo_env (its light fit), ~1 min with studio_dome_v1."""
    root = Path(root).resolve()
    if root.exists():
        raise ValueError("use a new empty package directory")
    if rig not in MATCHED_RIGS:
        raise ValueError(f"rig must be one of {MATCHED_RIGS}")
    inputs = root / "inputs"
    (inputs / "images").mkdir(parents=True)
    assets = []

    def copy(src, rel, want=None):
        dst = inputs / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        digest = sha(dst)
        if digest != sha(src) or (want is not None and digest != want):
            raise ValueError(f"{rel}: the copy or its source does not have the expected sha256")
        assets.append({"path": rel, "sha256": digest})
        return dst
    copy(source_scene, "scene.blend", scene_sha256)
    reference = Path(reference)
    manifest = read(reference / "manifest.json")
    doc = read(basis_json)
    targets = {n: r for n, r in doc["images"].items() if r.get("colourway") == "target"}
    if not targets:
        raise ValueError("the pose basis has no target photo")
    photos, paths = [], {}
    for name in sorted(targets):
        row, rec = manifest["files"].get(name), targets[name]
        if row is None or row["sha256"] != rec["image_sha256"]:
            raise ValueError(f"{name}: not in the reference manifest, or the manifest and the basis disagree")
        paths[name] = copy(reference / name, f"images/{name}", row["sha256"])
        with Image.open(paths[name]) as im:
            size = list(im.size)
        photos.append({"id": Path(name).stem.split("-", 1)[1], "path": f"images/{name}", "view": rec["view_kind"],
                       "size_px": size, "sha256": row["sha256"],
                       **({"note": side_note(rec)} if rec["view_kind"] == "side" else {})})
    (inputs / "brief.txt").write_text(brief_text(manifest.get("name") or reference.name, photos), encoding="utf-8")
    assets.append({"path": "brief.txt", "sha256": sha(inputs / "brief.txt")})
    copy(HERE / "instructions.txt", "instructions.txt")
    bdir = root / "basis"
    bdir.mkdir()
    write_json_atomic(bdir / "cameras.json", {**doc, "images": targets})
    basis = import_basis(root, bdir / "cameras.json", paths, reason="facet lab package (target photos only)")
    bound = basis_files(root, basis.id)
    write_json_atomic(inputs / "lab.json", {
        "schema": LAB_SCHEMA, "rig": rig, "product": manifest.get("name"), "assets": assets,
        "photos": [{k: v for k, v in p.items() if k != "note"} for p in photos],
        "source": {"scene": str(source_scene), "scene_sha256": scene_sha256,
                   "reference_manifest": str(reference / "manifest.json"),
                   "reference_manifest_sha256": sha(reference / "manifest.json")},
        "basis": {"source": str(basis_json), "source_sha256": sha(basis_json), "id": basis.id, "files": bound}})
    job = {"schema": "g3d.job/1", "name": f"{manifest.get('name')}: isolated facet lab", "rig": rig,
           "implementation": implementation(), "scope": "isolated facet lab on a copy, not production"}
    write_json_atomic(root / "job.json", job)
    lab = lab_class(root)
    try:
        prep = lab.prepare()
        if not lab.is_clean():
            raise ValueError("the prepared package is not clean")
        env = environment(root, basis.id) if rig == ENV_RIG else None
        if rig == ENV_RIG and env is None:
            raise ValueError("photo_env: the job's light was not fitted at prepare")
        job["environment"] = env
        write_json_atomic(root / "job.json", job)
        tools = registry(lab)
        write_json_atomic(root / "tools.json", [{"name": t.name, "description": t.description,
                                                 "parameters": t.params_json_schema} for t in tools])
        exit_checkpoint = lab.exit_checkpoint()
        if not exit_checkpoint["verified"]:
            raise ValueError("the prepared checkpoint does not verify")
        rev0 = lab.state["revisions"][0]
        write_json_atomic(root / "prepared.json", {
            "status": "prepared", "rig": rig, "prepare": prep, "start_checkpoint": lab.state["start_checkpoint"],
            "checkpoint": exit_checkpoint["id"], "exit_checkpoint": exit_checkpoint,
            "revision0": {"sha256": rev0["sha256"], "file": rev0["file"],
                          "template_report": (lab.state.get("original") or {}).get("template_report")},
            "compares": [c["compare_id"] for c in lab.state["compares"]], "environment": env, "paid_calls": 0})
    finally:
        lab.close()
    return {"status": "prepared", "path": str(root), "rig": rig, "photos": len(photos),
            "checkpoint": exit_checkpoint["id"], "revision0": rev0["sha256"][:8], "paid_calls": 0}


def basis_files(root: Path, basis_id: str) -> dict:
    """sha256 of the pose basis files a package binds (the source copy outside inputs/ and the frozen basis)."""
    files = ["basis/cameras.json", f"{C.CAMERAS_DIR}/{basis_id}/cameras.json", f"{C.CAMERAS_DIR}/{basis_id}/basis.json"]
    return {f: sha(root / f) for f in files}


def environment(root: Path, basis_id: str) -> dict | None:
    """The job's frozen photo_env light (seeing: work/compare/env-<basis>.json), as {file, weights_sha}."""
    p = root / "work" / "compare" / f"env-{basis_id}.json"
    if not p.is_file():
        return None
    return {"file": str(p.relative_to(root)), "weights_sha": read(p).get("weights_sha"), "sha256": sha(p)}


def check_package(root: Path, lab) -> None:
    """Refuse (ValueError) a package whose implementation, inputs, pose basis or frozen light changed, or which is
    not clean (a paid run starts from revision 0 of an untouched package)."""
    job = read(root / "job.json")
    if job.get("implementation") != implementation():
        raise ValueError("implementation changed since preparation; prepare a fresh package")
    lab.verify_inputs()
    basis = lab.manifest.get("basis") or {}
    if not basis.get("files") or basis_files(root, basis["id"]) != basis["files"]:
        raise ValueError("the pose basis changed since preparation; prepare a fresh package")
    if job.get("rig") != lab.rig:
        raise ValueError("job.json and lab.json name different rigs")
    if lab.rig == ENV_RIG:
        env = environment(root, basis["id"])
        if env is None or env != job.get("environment"):
            raise ValueError("the frozen photo_env light changed since preparation; prepare a fresh package")
    if not lab.is_clean():
        raise ValueError("a paid run starts only from a clean package (no facet tool has run); prepare a fresh one")
    head = lab.head_record()
    if head is None or head["revision"] != 0:
        raise ValueError("the package's head is not revision 0")


def instructions_text(root: Path, k: int) -> str:
    text = (Path(root) / "inputs" / "instructions.txt").read_text(encoding="utf-8")
    if text.count(K_TOKEN) != 1:
        raise ValueError(f"instructions.txt must state {K_TOKEN} once (finish precondition 1)")
    return text.replace(K_TOKEN, str(int(k)))


def references(lab) -> list:
    rows = lab.manifest["photos"]
    out = []
    for i, row in enumerate(rows, 1):
        p = lab.root / "inputs" / row["path"]
        with Image.open(p) as im:
            size = im.size
        out.append(ReferenceInput(label=f"TARGET PHOTO {row['id']} ({i} of {len(rows)})", path=row["path"],
                                  image_url=data_url(p), detail="high", view=row["view"], size_px=size))
    return out


def make_spec(root, run_id, settings, cap, *, write_request=False, lab=None, estimate_path=ESTIMATE, estimate=None):
    """The RunSpec of a paid run on a clean package (and, with write_request, its approval request with the facet
    estimate from estimate_path, or the given ``estimate`` block; nothing is approved here)."""
    root = Path(root).resolve()
    lab = lab or Lab(root)
    check_package(root, lab)
    tools = registry(lab, settings)
    instructions = instructions_text(root, settings.finish_k)
    brief = (root / "inputs" / "brief.txt").read_text(encoding="utf-8")
    photos = references(lab)
    contract = ToolContract(frozenset(TOOL_NAMES))
    problems = contract_problems(contract, instructions=instructions, brief=brief, tools=tools)
    if problems:
        raise ValueError(problems)
    request = A.build_request(root, instructions_sha256=instructions_sha256(instructions),
                              tool_registry_sha256=tool_registry_sha256(tools, settings),
                              settings=settings.approval_settings(),
                              run_inputs=A.run_input_binding(root, brief, photos))
    if write_request:
        run_dir = root / "runs" / run_id
        cost = estimate if estimate is not None else request_estimate(
            read(estimate_path), settings, c0_chars(instructions, brief, tools, photos), [p.size_px for p in photos])
        A.write_approval_request(run_dir, job_id=root.name, request=request, estimate=cost, suggested_cap_usd=cap)
        # the exact text sent (its sha256 is the request's instructions_sha256): LF, never the platform's newline
        (run_dir / "instructions-sent.txt").write_text(instructions, encoding="utf-8", newline="\n")
    note = f"The scene holds revision 0 at checkpoint {lab.s.head['id']}; facet_inspect shows it."
    spec = RunSpec(job_dir=root, run_id=run_id, instructions=instructions, tools=tools, brief=brief, photos=photos,
                   current_request_sha256=A.request_sha256(request), settings=settings, requested_cap_usd=cap,
                   scene_note=note, contract=contract)
    return spec, lab


# --------------------------------------------------------------------------------------------- estimate
def image_tokens(w: int, h: int) -> float:
    return TOKENS_PER_PATCH * math.ceil(w / PATCH_PX) * math.ceil(h / PATCH_PX) + TOKENS_PER_IMAGE


def c0_chars(instructions: str, brief: str, tools, photos) -> int:
    """Text characters of a first request: instructions, brief, tool schemas and one descriptor line per photo."""
    schemas = json.dumps([{"name": t.name, "description": t.description, "parameters": t.params_json_schema}
                          for t in tools], separators=(",", ":"))
    return len(instructions) + len(brief) + len(schemas) + 160 * len(photos) + 400


def first_request_tokens(chars: int, sizes) -> int:
    return math.ceil(TOKENS_PER_CHAR * chars + sum(image_tokens(w, h) for w, h in sizes))


def call_tokens(sample: dict) -> dict:
    """One recorded tool call: its growth of the context (arguments and output text, every image) and the part in
    images the context guard may replace."""
    imgs = [image_tokens(w, h) for w, h, _kind in sample["images"]]
    stub = [t for t, (_w, _h, kind) in zip(imgs, sample["images"]) if kind in STUBBED_KINDS]
    return {"tokens": TOKENS_PER_CHAR * (sample["text_chars"] + sample["args_chars"]) + sum(imgs),
            "stubbable": sum(stub), "stub_images": len(stub)}


def mix_step(samples: list, mix: dict) -> dict:
    """The mean growth per request of a call mix, from the rehearsal's successful calls of each tool."""
    by_tool: dict = {}
    for s in samples:
        if s["status"] in ("ok", "applied", "accepted"):
            by_tool.setdefault(s["tool"], []).append(call_tokens(s))
    total = sum(mix.values())
    step = {"tokens": 0.0, "stubbable": 0.0, "results": 0.0, "stub_images": 0.0}
    for tool, weight in mix.items():
        rows = by_tool.get(tool)
        if not rows:
            raise ValueError(f"the rehearsal has no successful {tool} call to price")
        share = weight / total
        for key in ("tokens", "stubbable", "stub_images"):
            step[key] += share * sum(r[key] for r in rows) / len(rows)
        step["results"] += share * sum(1 for r in rows if r["stub_images"]) / len(rows)
    return step


def project(c0: int, step: dict, requests: int, *, output: int, max_output_tokens: int,
            guard_tokens: int | None = CONTEXT_GUARD_TOKENS, tariff: Tariff | None = None) -> dict:
    """One modelled run, request by request with the frozen tariff (the 272k step included): request i's input is
    the previous input (cached) plus the mean growth (written); once a counted input reaches the guard, the next
    request replaces the images of all but GUARD_KEEP_RESULTS compare / variant results by stubs and is written
    afresh after the brief."""
    tariff = tariff or Tariff.frozen()
    prev, spent, need = None, 0, 0
    results, armed = 0.0, False
    guard_at = long_at = None
    peak = 0
    per_result = step["stubbable"] / step["results"] if step["results"] else 0.0
    imgs_per_result = step["stub_images"] / step["results"] if step["results"] else 0.0
    for i in range(requests):
        if prev is None:
            inp, cached = c0, 0
        else:
            new, cached = prev + step["tokens"], prev
            if armed:
                drop = max(0.0, results - GUARD_KEEP_RESULTS)
                new -= drop * (per_result - imgs_per_result * TOKENS_PER_CHAR * STUB_CHARS)
                cached, armed, guard_at = c0, False, i + 1
            inp = math.ceil(new)
            results += step["results"]
        cached = min(cached, inp)
        usage = {"input_tokens": inp, "output_tokens": output, "total_tokens": inp + output,
                 "input_tokens_details": {"cached_tokens": cached, "cache_write_tokens": inp - cached},
                 "output_tokens_details": {"reasoning_tokens": 0}}
        settled, hold = tariff.settle_micro(usage), tariff.reserve_micro(inp, max_output_tokens)
        need = max(need, spent + hold)
        spent += settled
        peak = max(peak, inp)
        if long_at is None and inp > LONG_CONTEXT_INPUT_TOKENS:
            long_at = i + 1
        if guard_tokens is not None and guard_at is None and not armed and inp >= guard_tokens:
            armed = True
        prev = inp
    return {"requests": requests, "expected_micro_usd": spent, "expected_usd": usd_text(spent),
            "required_cap_micro_usd": need, "last_input_tokens": prev, "peak_input_tokens": peak,
            "context_guard_request": guard_at, "first_long_context_request": long_at}


def facet_estimate(rehearsal: dict, *, requests=RUN_LENGTHS, max_output_tokens: int = 16_384) -> dict:
    """The estimate table (estimate.json): runs of ``requests`` replies at effort high and max, the typical mix at the
    expected output and the edit-heavy mix at the high output (the range)."""
    first = rehearsal["first_request"]
    c0 = first_request_tokens(first["text_chars"], first["image_sizes"])
    samples = rehearsal["tool_outputs"]
    steps = {name: mix_step(samples, mix) for name, mix in MIXES.items()}
    rows = []
    for n in requests:
        for effort in ("high", "max"):
            exp = project(c0, steps["typical"], n, output=OUTPUT_TOKENS["expected"],
                          max_output_tokens=max_output_tokens)
            high = project(c0, steps["edit_heavy"], n, output=OUTPUT_TOKENS["high"],
                           max_output_tokens=max_output_tokens)
            out_share = Tariff.frozen().settle_micro({"input_tokens": 0, "output_tokens": OUTPUT_TOKENS["expected"] * n,
                                                      "total_tokens": OUTPUT_TOKENS["expected"] * n})
            rows.append({"requests": n, "effort": effort, "expected": exp, "high": high,
                         "suggested_cap_usd": usd_text(suggested_cap_micro(high["required_cap_micro_usd"])),
                         "output_micro_usd": out_share,
                         "text": f"{n} replies at effort {effort}: expected {cents_text(exp['expected_micro_usd'])}, "
                                 f"up to {cents_text(high['expected_micro_usd'])} (every reply an edit)"})
    per_tool = {}
    for s in samples:
        t = call_tokens(s)
        per_tool.setdefault(s["tool"], []).append({"status": s["status"], "text_chars": s["text_chars"],
                                                   "args_chars": s["args_chars"],
                                                   "images": [[w, h, k] for w, h, k in s["images"]],
                                                   "tokens": round(t["tokens"]), "stubbable": round(t["stubbable"])})
    return {"schema": ESTIMATE_SCHEMA, "price_version": Tariff.frozen().price_version,
            "formula": "tokens = 0.42 x text chars + per image 0.975 x ceil(W/32) x ceil(H/32) + 112",
            "first_request": {**first, "tokens": c0, "v1_check": V1_FIRST_REQUEST},
            "output_tokens_per_request": OUTPUT_TOKENS, "mixes": MIXES,
            "steps": {k: {kk: round(vv, 1) for kk, vv in v.items()} for k, v in steps.items()},
            "context_guard_tokens": CONTEXT_GUARD_TOKENS, "long_context_tokens": LONG_CONTEXT_INPUT_TOKENS,
            "rows": rows, "tool_outputs": per_tool,
            "notes": ["No gpt-6-astra ledger at effort high exists; high is priced with the max numbers (an upper "
                      "bound). output_micro_usd is the whole expected output cost, the most effort high can save.",
                      "The cap is enforced by the guard request by request, independently of this forecast."]}


def request_estimate(table: dict, settings, chars: int, sizes) -> dict:
    """The approval request's estimate block: this package's first request and the run's turns (60 when the run has
    no reply cap) with the growth measured by the rehearsal (estimate.json)."""
    c0 = first_request_tokens(chars, sizes)
    n = settings.max_turns or 60
    steps = {k: {kk: float(vv) for kk, vv in v.items()} for k, v in table["steps"].items()}
    exp = project(c0, steps["typical"], n, output=OUTPUT_TOKENS["expected"],
                  max_output_tokens=settings.max_output_tokens, guard_tokens=settings.context_guard_tokens)
    high = project(c0, steps["edit_heavy"], n, output=OUTPUT_TOKENS["high"],
                   max_output_tokens=settings.max_output_tokens, guard_tokens=settings.context_guard_tokens)
    suggested = suggested_cap_micro(high["required_cap_micro_usd"])
    text = (f"Expected {cents_text(exp['expected_micro_usd'])} (up to {cents_text(high['expected_micro_usd'])} if "
            f"every reply is an edit) for {n} Astra replies at effort {settings.effort}.")
    if settings.max_turns is None:
        text = "Illustrative 60-reply projection; this run has no reply cap. " + text
    return {"schema": ESTIMATE_SCHEMA, "first_request_tokens": c0, "requests": n, "effort": settings.effort,
            "expected_micro_usd": exp["expected_micro_usd"], "expected_usd": exp["expected_usd"],
            "range_usd": [exp["expected_usd"], high["expected_usd"]], "expected": exp, "high": high,
            "suggested_cap_micro_usd": suggested, "suggested_cap_usd": usd_text(suggested),
            "source": {"table": table.get("schema"), "steps": table["steps"]},
            "text": text, "limitation": "A forecast from the rehearsal's tool outputs and the fitted token formula; "
                                        "effort high is priced as max. The cap is enforced independently. No "
                                        "approval has been recorded."}


# --------------------------------------------------------------------------------------------- main
def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("action", choices=["prepare", "estimate", "request", "run"])
    p.add_argument("--job", type=Path)
    p.add_argument("--rig", default=DEFAULT_RIG, choices=MATCHED_RIGS)
    p.add_argument("--rehearsal", type=Path, help="the T5 rehearsal.json (estimate)")
    p.add_argument("--out", type=Path, default=ESTIMATE, help="estimate.json to write (estimate)")
    p.add_argument("--estimate", type=Path, default=ESTIMATE, help="estimate.json to read (request)")
    p.add_argument("--run-id", default="facet-astra-001")
    p.add_argument("--effort", choices=["high", "max"])
    turns = p.add_mutually_exclusive_group()
    turns.add_argument("--turns", type=int)
    turns.add_argument("--no-turn-cap", action="store_true")
    p.add_argument("--cap")
    p.add_argument("--k", type=int, default=4, help="facet_finish: distinct compared heads (owner per run)")
    p.add_argument("--fit-output-to-budget", action="store_true",
                   help="Reduce the next response ceiling when needed to use the remaining cap without a fixed reserve")
    p.add_argument("--env-file", type=Path, help="Explicit local credential file; its contents are never logged")
    a = p.parse_args(argv)
    try:
        if a.action == "prepare":
            if a.job is None:
                p.error("prepare requires --job")
            result = prepare(a.job, rig=a.rig)
        elif a.action == "estimate":
            if a.rehearsal is None:
                p.error("estimate requires --rehearsal")
            table = facet_estimate(read(a.rehearsal))
            table["rehearsal"] = {"path": str(a.rehearsal), "sha256": sha(a.rehearsal)}
            write_json_atomic(a.out, table)
            result = {"status": "estimated", "path": str(a.out), "rows": [r["text"] for r in table["rows"]]}
        else:
            if a.job is None or a.effort is None or (a.turns is None and not a.no_turn_cap) or a.cap is None:
                p.error("request/run requires --job, --effort, --turns or --no-turn-cap, and --cap")
            settings = run_settings(effort=a.effort, max_turns=a.turns, fit_output_to_budget=a.fit_output_to_budget,
                                    finish_k=a.k)
            spec, lab = make_spec(a.job, a.run_id, settings, a.cap, write_request=a.action == "request",
                                  estimate_path=a.estimate)
            spec.env_file = a.env_file
            result = {"status": "approval_request_ready", "request_sha256": spec.current_request_sha256,
                      "run_dir": str(Path(a.job).resolve() / "runs" / a.run_id), "approved": False} \
                if a.action == "request" else asyncio.run(run_astra(spec, scene=lab))
    except (A.ApprovalRefused, RunRefused) as exc:
        print(json.dumps({"status": "refused", "type": type(exc).__name__, "message": str(exc)}, indent=2))
        sys.exit(exc.exit_code)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
