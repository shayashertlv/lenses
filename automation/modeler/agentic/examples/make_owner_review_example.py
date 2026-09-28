"""Write the two scripted rounds of the owner review example from test-pilot-002's revision r0006 (no program text is
invented here: every module is read from the job's working copy and checked against the revision row's sha256).

    python -m modeler.agentic.examples.make_owner_review_example            (writes owner_review_round1.json / _round2.json here)

Round 1 (``start --script owner_review_round1.json``): edit_program with r0006's exact seven modules (base null, replace,
build_now), fetch_pending_images, request_delivery of r0001; the job then waits in awaiting_owner.
Round 2 (``owner-review --changes ... --script owner_review_round2.json``): a patch of the lens tint in the materials module
(transmission_top_rgb / transmission_bottom_rgb of gl.lens_optics) toward lens_transmission_recommended (0.59, 0.485, 0.38),
build_now, fetch_pending_images, request_delivery of r0002; the job waits again.

The source job is only read: its database is copied (with its WAL) into a temporary folder and opened there.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile

from ...candidates import MODULE_ORDER
from ...paths import MODELER_DATA
from ..demo import _resp

HERE = Path(__file__).resolve().parent
SOURCE_JOB = MODELER_DATA / "agentic" / "test-pilot-002"
SOURCE_REVISION = "r0006"
# the lens tint of r0006's materials module and the one the owner's round asks for (summary.lens_colour.lens_transmission_recommended)
TINT_FIND = "transmission_top_rgb=(0.75,0.725,0.67),transmission_bottom_rgb=(0.75,0.725,0.67)"
TINT_REPLACE = "transmission_top_rgb=(0.59,0.485,0.38),transmission_bottom_rgb=(0.59,0.485,0.38)"
OWNER_TEXT = "the lens colour is too light, the frame is too clear"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def revision_row(job_dir: Path, rid: str) -> dict:
    """The revision row, read from a copy of the job database (the source is never opened)."""
    with tempfile.TemporaryDirectory(prefix="orx-") as tmp:
        for suffix in ("", "-wal"):
            src = job_dir / f"job.sqlite3{suffix}"
            if src.is_file():
                shutil.copyfile(src, Path(tmp) / f"job.sqlite3{suffix}")
        conn = sqlite3.connect(str(Path(tmp) / "job.sqlite3"))
        try:
            row = conn.execute("SELECT id, program_set_sha256, modules_json FROM revisions WHERE id = ?", (rid,)).fetchone()
        finally:
            conn.close()
    if row is None:
        raise SystemExit(f"no revision {rid} in {job_dir}")
    return {"id": row[0], "program_set_sha256": row[1], "modules": json.loads(row[2])}


def read_modules(job_dir: Path = SOURCE_JOB, rid: str = SOURCE_REVISION) -> dict[str, str]:
    """The revision's modules from revisions/<rid>/program/, each verified against the revision row."""
    row = revision_row(job_dir, rid)
    pdir = job_dir / "revisions" / rid / "program"
    modules = {}
    for name in MODULE_ORDER:
        p = pdir / f"{name}.py"
        if name not in row["modules"]:
            continue
        text = p.read_bytes().decode("utf-8")
        if sha(text) != row["modules"][name]["sha256"]:
            raise SystemExit(f"{p}: sha256 {sha(text)[:12]} is not the revision row's {row['modules'][name]['sha256'][:12]}")
        modules[name] = text
    return modules


def module_specs(**over) -> dict:
    return {n: over.get(n, {"mode": "inherit", "content": None, "expected_base_sha256": None}) for n in MODULE_ORDER}


def step(call_id: str, name: str, args: dict, expect: dict | None = None) -> dict:
    s = {"endpoint": "responses", "body": _resp(call_id, name, args, rs=f"rs_{call_id}")}
    if expect:
        s["expect"] = expect
    return s


def round1(modules: dict[str, str]) -> dict:
    specs = module_specs(**{n: {"mode": "replace", "content": t, "expected_base_sha256": None} for n, t in modules.items()})
    return {"note": f"round 1 of the owner review example: {SOURCE_JOB.name} {SOURCE_REVISION}'s exact modules as r0001, then request_delivery",
            "source": {"job": SOURCE_JOB.name, "revision": SOURCE_REVISION, "modules_sha256": {n: sha(t) for n, t in modules.items()}},
            "steps": [step("or1_edit", "edit_program", {"base_revision_id": None, "modules": specs, "rationale": f"{SOURCE_REVISION} of {SOURCE_JOB.name}, unchanged",
                                                        "expected_changes": ["the owner's accepted-in-principle pilot model"], "build_now": True,
                                                        "deliver_if_compatible": False}),
                      step("or1_fetch", "fetch_pending_images", {"max_images": 14}, {"contains_call_output": "or1_edit"}),
                      step("or1_deliver", "request_delivery", {"revision_id": "r0001", "status_claim": "best_effort",
                                                               "note": f"{SOURCE_REVISION} rebuilt unchanged for the owner's review"},
                           {"contains_call_output": "or1_fetch"})]}


def round2(modules: dict[str, str]) -> dict:
    materials = modules["materials"]
    if materials.count(TINT_FIND) != 1:
        raise SystemExit(f"the materials module does not hold the lens tint {TINT_FIND!r} exactly once")
    patch = json.dumps([{"find": TINT_FIND, "replace": TINT_REPLACE}])
    specs = module_specs(materials={"mode": "patch", "content": patch, "expected_base_sha256": sha(materials)})
    return {"note": "round 2 of the owner review example: the lens tint toward lens_transmission_recommended (0.59, 0.485, 0.38), then request_delivery",
            "owner_text": OWNER_TEXT,
            "steps": [step("or2_edit", "edit_program", {"base_revision_id": "r0001", "modules": specs,
                                                        "rationale": "the owner: the lens colour is too light; the tint set to lens_transmission_recommended",
                                                        "expected_changes": ["a darker, warmer lens"], "build_now": True, "deliver_if_compatible": False},
                           {"has_text": ["Owner review, round 1", "lens_transmission_recommended"]}),
                      step("or2_fetch", "fetch_pending_images", {"max_images": 14}, {"contains_call_output": "or2_edit"}),
                      step("or2_deliver", "request_delivery", {"revision_id": "r0002", "status_claim": "improved",
                                                               "note": "lens tint changed to the recommended transmission; the crystal body left as it was"},
                           {"contains_call_output": "or2_fetch"})]}


def write(out_dir: Path = HERE) -> tuple[Path, Path]:
    modules = read_modules()
    p1, p2 = out_dir / "owner_review_round1.json", out_dir / "owner_review_round2.json"
    p1.write_text(json.dumps(round1(modules), indent=1) + "\n", encoding="utf-8")
    p2.write_text(json.dumps(round2(modules), indent=1) + "\n", encoding="utf-8")
    return p1, p2


def main(argv=None) -> int:
    for p in write(Path(argv[0]) if argv else HERE):
        print(p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
