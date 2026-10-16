"""The facet lab's package entry points (agent/temple_lab/cli.py; plan/WP6_V3.md §6, WP6_FACETS.md §4.3-4.4): prepare
with fakes (no Blender), the clean-package and binding refusals of make_spec, the approval request's settings binding,
the v3 prompt's contract (tool names, {K}, no job facts, no retired phrase, the length cap, the finish text against
state.finish_missing), the v3 brief (camera lineage, reading precision, the theta lever, camera-model conflicts, the
light, the scope, the side views' outline offsets) and the facet-run estimate with the v3 tool mix and images. The
real-Blender path is T5 (tests/proofs/astra_temple_lab.py).
"""
import hashlib
import json
import math
import re
import threading
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

pytest.importorskip("agents")
from glasses3d.agent import approvals as A  # noqa: E402
from glasses3d.agent import loop as L  # noqa: E402
from glasses3d.agent import seeing  # noqa: E402
from glasses3d.agent import budget as B  # noqa: E402
from glasses3d.tests.fixtures import temple_lab_prada as PRADA
from glasses3d.agent.temple_lab import cli  # noqa: E402
from glasses3d.agent.temple_lab import sheets as SH  # noqa: E402
from glasses3d.agent.temple_lab import tools as T  # noqa: E402
from glasses3d.agent.temple_lab.lab import LAB_SCHEMA, Lab  # noqa: E402
from glasses3d.g3d.temple import fixtures as FX  # noqa: E402
from glasses3d.tests.agent.test_temple_lab import FakeBasis, FakeSeeing, FakeSession  # noqa: E402

KINDS = {"01": ("side", -90.8, 13.7), "02": ("angled", 30.0, 11.4), "03": ("top", 0.3, 59.9),
         "04": ("front", 0.0, 10.3), "05": ("side", 90.6, 13.6), "06": ("rear", 180.0, 8.8)}   # view, yaw, pitch


def sha(p) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def sources(tmp: Path) -> dict:
    """A fake original scene, reference folder (manifest + 6 target photos), the pose basis and the black photos as
    the real package has them: three colourway-variant basis images with supplied mattes and transported cameras (the
    basis's fit_status: B1 from 05, B2 from 02, B3 from 04, theta copied), B1 side (beside the reference photos), B2
    angled and B3 front (from another folder); no reference-only photo."""
    ref = tmp / "reference"
    ref.mkdir(parents=True)
    scene = tmp / "scene.blend"
    scene.write_bytes(b"BLENDER fake scene")
    files, images = {}, {}
    for k, (pid, (kind, yaw, pitch)) in enumerate(KINDS.items()):
        name = f"reference-{pid}.png"
        Image.new("RGB", (64 + 8 * k, 40), (40 * k, 120, 200)).save(ref / name)
        files[name] = {"sha256": sha(ref / name), "label": kind}
        images[name] = {"image": str(ref / name), "image_sha256": files[name]["sha256"], "view_kind": kind,
                        "colourway": "target", "camera": {"yaw": yaw, "pitch": pitch},
                        "theta_deg": {"+1": 0, "-1": 0}, "hinge_axis_xz_mm": {"+1": [60, 0], "-1": [-60, 0]}}
    (tmp / "mattes").mkdir()
    black = tmp / "black"
    black.mkdir()
    colourway = []
    for pid, name, src, kind, yaw, pitch, size, fit in (
            ("B1", "black-side.png", ref / "black-side.png", "side", 90.0, 13.6, (30, 30),
             "camera transported from 05"),
            ("B2", "black-angled.png", black / "B2.png", "angled", 30.0, 11.4, (48, 32),
             "camera transported from 02 by image similarity (contour 0.82 px, IoU 0.992); theta copied from 02"),
            ("B3", "black-front.png", black / "B3.png", "front", 0.0, 10.3, (48, 32),
             "camera transported from 04 by image similarity (contour 1.09 px, IoU 0.992); theta copied from 04")):
        Image.new("RGB", size, (5, 5, 5 + len(kind))).save(src)
        matte = tmp / "mattes" / name
        Image.new("L", size, 255).save(matte)
        images[name] = {"image": str(src), "image_sha256": sha(src), "view_kind": kind, "colourway": "variant",
                        "camera": {"yaw": yaw, "pitch": pitch}, "matte_mode": "supplied",
                        "matte_file": f"mattes/{name}", "matte_file_sha256": sha(matte), "fit_status": fit,
                        "theta_deg": {"+1": 0, "-1": 0}, "hinge_axis_xz_mm": {"+1": [60, 0], "-1": [-60, 0]},
                        "theta_observed": {"+1": False, "-1": False}}
        colourway.append({"id": pid, "image": name, "colour": "black", "finish": "glossy", "sha256": sha(src)})
    (ref / "manifest.json").write_text(json.dumps({"name": "Test Frame 1", "files": files}), encoding="utf-8")
    basis = tmp / "cameras.json"
    basis.write_text(json.dumps({"model": {"skip": []}, "images": images}), encoding="utf-8")
    return {"scene": scene, "scene_sha256": sha(scene), "reference": ref, "basis_json": basis,
            "colourway_photos": tuple(colourway), "reference_photos": ()}


def reference_only(tmp: Path, pid: str = "R1") -> dict:
    """A photo without a camera (role reference: photo_image only), as a job whose extra photo has none passes it."""
    p = tmp / f"{pid}.png"
    Image.new("RGB", (40, 24), (9, 9, 9)).save(p)
    return {"id": pid, "view": "angled", "colour": "black", "source": p, "path": f"images/{pid}-angled.png",
            "sha256": sha(p)}


def fake_basis() -> FakeBasis:
    """test_temple_lab's FakeBasis (targets + black-side) with the package's other calibrated black photos."""
    b = FakeBasis()
    for rid, kind in (("black-angled", "angled"), ("black-front", "front")):
        b.refs[rid] = {"view_kind": kind, "camera": None, "colourway": "variant"}
    return b


def fake_import(root, cameras_json, photos, *, reason):
    d = Path(root) / "cameras" / "basis-01"
    d.mkdir(parents=True)
    (d / "cameras.json").write_bytes(Path(cameras_json).read_bytes())
    (d / "basis.json").write_text(json.dumps({"id": "basis-01", "photos": sorted(photos), "reason": reason}),
                                  encoding="utf-8")
    return SimpleNamespace(id="basis-01")


class PrepLab:
    """Lab.prepare's outward behaviour: revision 0, a verified head and, with the photo_env rig only, the job's light
    frozen in work/compare."""
    made: list = []

    def __init__(self, root):
        self.root = Path(root)
        self.manifest = json.loads((self.root / "inputs" / "lab.json").read_text(encoding="utf-8"))
        assert self.manifest["schema"] == LAB_SCHEMA
        self.state = {"revisions": [{"sha256": "a" * 64, "file": "revisions/r0000.json"}], "start_checkpoint": "c0000",
                      "compares": [{"compare_id": "cmp001"}, {"compare_id": "cmp002"}],
                      "original": {"template_report": {"distance": "x"}}}
        self.closed = False
        self.lock = threading.Lock()
        PrepLab.made.append(self)

    def prepare(self):
        if self.manifest["rig"] == "photo_env":
            p = self.root / "work" / "compare" / "env-basis-01.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps({"weights_sha": "w" * 16}), encoding="utf-8")
        return {"status": "prepared", "checkpoint": "c0001"}

    def is_clean(self):
        return True

    def photo_ids(self):
        return [p["id"] for p in self.manifest["photos"] if p["role"] == "target"]

    def compare_ids(self):
        return self.photo_ids() + list(self.colourway_refs())

    def colourway_refs(self):
        return {p["id"]: p["basis_ref"] for p in self.manifest["photos"] if p["role"] == "colourway"}

    def variant_photos_max(self):
        """Lab's own rule over this fake's photos (tools.registry bounds facet_variants' photos with it)."""
        return Lab.variant_photos_max(self)

    def image_ids(self):
        return [p["id"] for p in self.manifest["photos"]]

    def exit_checkpoint(self):
        return {"id": "c0001", "sha256": "0" * 64, "verified": True}

    def close(self):
        self.closed = True


def package(tmp_path: Path, rig: str = cli.DEFAULT_RIG) -> tuple[Path, dict]:
    src = sources(tmp_path)
    root = tmp_path / "job"
    out = cli.prepare(root, rig=rig, source_scene=src["scene"], scene_sha256=src["scene_sha256"],
                      reference=src["reference"], basis_json=src["basis_json"], lab_class=PrepLab,
                      import_basis=fake_import, colourway_photos=src["colourway_photos"],
                      reference_photos=src["reference_photos"], photo_ids=PRADA.PHOTO_IDS,
                      camera_conflicts=PRADA.CAMERA_CONFLICTS, reading_precision=PRADA.READING_PRECISION)
    return root, out


def lab_on(root: Path) -> Lab:
    """A real Lab on the prepared package with a fake scene and compare (revision 0 = prada_default, applied and
    compared by the prepare step), as test_temple_lab.make_lab does."""
    s = FakeSession(root)
    lab = Lab(root, session=s, seeing=FakeSeeing(s, root))
    lab._basis = fake_basis()
    lab.state["start_checkpoint"], lab.state["start_fingerprint"] = "c0000", "fp-original"
    lab.state["original"] = {"footprints": {"decals": [], "hinge": []}}
    rid, _ = lab._register(FX.prada_default(), parent=None, kind="template", label="revision 0")
    assert lab._apply("prepare-r0", rid, tool="prepare")["status"] == "ok"
    assert lab._compare("prepare-r0", lab.photo_ids(), by="prepare")["status"] == "ok"
    lab.save()
    return lab


# --------------------------------------------------------------------------------------------- prepare
def test_the_default_rig_is_the_owners_choice_and_fits_no_light(tmp_path):
    """The owner's light choice of 2026-10-11 ("c is definitely the best") = studio_dome_v1 is the package default:
    prepare fits no photo_env light (job.json environment null) and make_spec needs none."""
    assert cli.DEFAULT_RIG == "studio_dome_v1"
    root, out = package(tmp_path)
    assert out["rig"] == "studio_dome_v1"
    job = json.loads((root / "job.json").read_text(encoding="utf-8"))
    assert job["rig"] == "studio_dome_v1" and job["environment"] is None
    assert json.loads((root / "inputs" / "lab.json").read_text(encoding="utf-8"))["rig"] == "studio_dome_v1"
    assert not (root / "work" / "compare").exists()
    assert json.loads((root / "prepared.json").read_text(encoding="utf-8"))["environment"] is None
    spec, _lab = cli.make_spec(root, "r", T.run_settings(effort="max", max_turns=40), "10", lab=lab_on(root))
    assert spec.settings.finish_tool == "facet_finish"


def test_prepare_writes_an_isolated_bound_package(tmp_path):
    root, out = package(tmp_path, rig="photo_env")
    assert out["status"] == "prepared" and out["photos"] == 9 and out["paid_calls"] == 0 and PrepLab.made[-1].closed
    inputs = root / "inputs"
    images = sorted(p.name for p in (inputs / "images").iterdir())
    assert images == ["black-angled.png", "black-front.png", "black-side.png"] + \
        [f"reference-0{k}.png" for k in range(1, 7)]                       # the targets and the black photos
    assert sorted(p.name for p in inputs.iterdir()) == ["brief.txt", "images", "instructions.txt", "lab.json",
                                                         "scene.blend"]
    assert sha(inputs / "scene.blend") == sha(tmp_path / "scene.blend")
    lab_json = json.loads((inputs / "lab.json").read_text(encoding="utf-8"))
    assert lab_json["schema"] == LAB_SCHEMA and lab_json["rig"] == "photo_env"
    assets = {a["path"]: a["sha256"] for a in lab_json["assets"]}
    assert assets["images/reference-05.png"] == sha(inputs / "images/reference-05.png")
    assert assets["images/black-side.png"] == sha(tmp_path / "reference" / "black-side.png")
    assert assets["images/black-front.png"] == sha(tmp_path / "black" / "B3.png")      # byte copies, sha-bound
    rows = {p["id"]: p for p in lab_json["photos"]}
    assert list(rows) == ["01", "02", "03", "04", "05", "06", "B1", "B2", "B3"]
    assert [rows[k]["role"] for k in rows] == ["target"] * 6 + ["colourway"] * 3       # every black photo calibrated
    assert rows["B1"]["basis_ref"] == "black-side" and rows["B1"]["path"] == "images/black-side.png"
    assert rows["B2"]["basis_ref"] == "black-angled" and rows["B2"]["path"] == "images/black-angled.png"
    assert rows["B2"]["view"] == "angled" and rows["B3"]["view"] == "front" and rows["B3"]["colour"] == "black"
    assert rows["B3"]["basis_ref"] == "black-front" and rows["B3"]["source"] == str(tmp_path / "black" / "B3.png")
    blacks = ["black-angled.png", "black-front.png", "black-side.png"]
    basis = json.loads((root / "basis" / "cameras.json").read_text(encoding="utf-8"))
    assert sorted(basis["images"]) == blacks + [f"reference-0{k}.png" for k in range(1, 7)]
    for name in blacks:                                                  # each black photo's supplied matte, bound
        assert sha(root / "basis" / "mattes" / name) == basis["images"][name]["matte_file_sha256"]
    assert sorted(json.loads((root / "cameras" / "basis-01" / "basis.json").read_text(encoding="utf-8"))["photos"]) \
        == blacks + [f"reference-0{k}.png" for k in range(1, 7)]
    assert set(lab_json["basis"]["files"]) == {"basis/cameras.json", "cameras/basis-01/cameras.json",
                                               "cameras/basis-01/basis.json"} | {f"basis/mattes/{n}" for n in blacks}
    job = json.loads((root / "job.json").read_text(encoding="utf-8"))
    assert job["implementation"] == cli.implementation() and job["rig"] == "photo_env"
    assert job["environment"]["weights_sha"] == "w" * 16
    assert [t["name"] for t in json.loads((root / "tools.json").read_text(encoding="utf-8"))] == list(T.TOOL_NAMES)
    prepared = json.loads((root / "prepared.json").read_text(encoding="utf-8"))
    assert prepared["checkpoint"] == "c0001" and prepared["revision0"]["sha256"] == "a" * 64
    assert rows["B1"]["finish"] == "glossy" and "finish" not in rows["05"]          # job data, printed by the brief
    brief = (inputs / "brief.txt").read_text(encoding="utf-8")
    assert brief.startswith("Product: Test Frame 1. The target photos (01, 02, 03, 04, 05, 06) show the colourway to "
                            "match. Photos B1, B2, B3 show the same model in glossy black (the materials never change")
    assert ("- 05: side; camera yaw +90.6, pitch +13.6; the charted temple's outer face square-on (14 deg); its "
            "compares print the charted temple's bottom outline offsets and, for its top, the top face's far edge "
            "minus its median (the view looks down on the top face)") in brief
    assert ("- 01: side; camera yaw -90.8, pitch +13.7; the mirrored temple's outer face, from its own side, "
            "square-on (14 deg); its compares print the mirrored temple's bottom outline offsets") in brief
    assert ("- B1: side, glossy black; camera yaw +90.0, pitch +13.6; the charted temple's outer face square-on (14 "
            "deg); its compares print the charted temple's bottom outline offsets and, for its top, the top face's "
            "far edge minus its median (the view looks down on the top face); camera moved from 05's by matching the "
            "two photos' silhouettes, the temples' hinge opening as in 05; not checked against the model") in brief
    assert ("- B2: angled, glossy black; camera yaw +30.0, pitch +11.4; the charted temple's outer face at a "
            "moderate angle (61 deg); camera moved from 02's") in brief
    assert "Front and rear views look along creases that run front to back: 04, 06, B3 (cameras within 25 deg" in brief
    assert ("- 03: top; camera yaw +0.3, pitch +59.9; the outer faces edge-on (90 deg); faces that point straight up "
            "at 30 deg from square-on\n") in brief                        # v3: the top view prints no numbers
    assert "two temples): 01, 05, B1 | 02, B2 | 03 | 04, B3 | 06." in brief       # B1/B2/B3 share 05/02/04's view
    assert ("square-on (under 30 deg): 01, 05, B1. At a moderate angle (30 to 75 deg): 02, B2. Edge-on (75 deg or "
            "more): 03, 04, 06, B3.") in brief
    assert "Outline offsets (photo minus model, mm) are printed for 01, 05, B1 only." in brief
    assert "Glossy photos: B1, B2, B3." in brief
    assert "No finish is given for 01, 02, 03, 04, 05, 06." in brief       # the targets are not called matte
    assert (f"The renders use the job's fixed light, photo_env: {cli.RIG_TEXT['photo_env']}. The photos' light is "
            "unknown. " + cli.LIGHT_TEXT) in brief                         # the rig and what it does not reproduce
    assert ("Cameras: 01, 02, 03, 04, 05 and 06 each have a camera fitted on its own. The cameras of B1, B2 and B3 "
            "were not fitted: each is another photo's camera (B1's from 05, B2's from 02 and B3's from 04)") in brief
    # the job's data, as prepare passes it (CAMERA_CONFLICTS, READING_PRECISION), and the theta lever
    for line in (cli.precision_text(PRADA.READING_PRECISION), cli.theta_text(),
                 *(cli.conflict_text(c) for c in PRADA.CAMERA_CONFLICTS)):
        assert line + "\n" in brief, line
    assert "What you may change is the whole temple from the hinge to its tip" in brief
    assert "The real temples' edge rounding (roll_mm) is not known" in brief
    low = brief.lower()
    for gone in ("reference only", "acetate", "far more clearly", "px per mm", "outer-edge") + cli.RETIRED_PHRASES:
        assert gone not in low, gone                                     # no product claim, no retired line
    assert (inputs / "instructions.txt").read_bytes() == (cli.HERE / "instructions.txt").read_bytes()


def test_a_reference_only_photo_is_packaged_for_photo_image(tmp_path):
    """A photo without a camera (none for Prada now) still packages: role reference, no basis_ref, a byte copy under
    inputs/images, not in the basis; the brief and the first message say it is reference only."""
    src = sources(tmp_path)
    extra = reference_only(tmp_path)
    root = tmp_path / "job"
    cli.prepare(root, source_scene=src["scene"], scene_sha256=src["scene_sha256"], reference=src["reference"],
                basis_json=src["basis_json"], lab_class=PrepLab, import_basis=fake_import,
                colourway_photos=src["colourway_photos"], reference_photos=(extra,), photo_ids=PRADA.PHOTO_IDS)
    lab_json = json.loads((root / "inputs" / "lab.json").read_text(encoding="utf-8"))
    rows = {p["id"]: p for p in lab_json["photos"]}
    assert list(rows)[-4:] == ["B1", "B2", "B3", "R1"] and rows["R1"]["role"] == "reference"
    assert "basis_ref" not in rows["R1"] and rows["R1"]["path"] == extra["path"]
    assert {a["path"]: a["sha256"] for a in lab_json["assets"]}[extra["path"]] == extra["sha256"]
    assert "R1-angled.png" not in json.loads((root / "basis" / "cameras.json").read_text(encoding="utf-8"))["images"]
    brief = (root / "inputs" / "brief.txt").read_text(encoding="utf-8")
    assert "- R1: angled, black; no calibrated camera: photo_image only" in brief
    assert "B3 | 06." in brief and "R1 |" not in brief                     # no camera: in no direction group
    labels = [p.label for p in cli.references(lab_on(root))]
    assert labels[-1] == "BLACK PHOTO R1 (4 of 4; the same model in black, reference only, no camera)"


def test_the_pinned_black_photos():
    """The package's black photos are pinned by sha256, each a colourway-variant image of the pose basis with its
    camera transported from the target photo of the same pose: B1 = black-side (from 05), B2 = black-angled (from
    02), B3 = black-front (from 04); no reference-only photo; distinct ids and input paths."""
    assert [p["id"] for p in PRADA.COLOURWAY_PHOTOS] == ["B1", "B2", "B3"] and PRADA.REFERENCE_PHOTOS == ()
    assert [(p["image"], p["sha256"][:8]) for p in PRADA.COLOURWAY_PHOTOS] == [
        ("black-side.png", "6eddabbb"), ("black-angled.webp", "5705c2ca"), ("black-front.webp", "e89d19f4")]
    assert len({p["image"] for p in PRADA.COLOURWAY_PHOTOS}) == 3
    assert {(p["colour"], p["finish"]) for p in PRADA.COLOURWAY_PHOTOS} == {("black", "glossy")}
    if PRADA.BASIS_JSON.is_file():
        images = json.loads(PRADA.BASIS_JSON.read_text(encoding="utf-8"))["images"]
        for p, src in zip(PRADA.COLOURWAY_PHOTOS, ("05", "02", "04")):
            rec = images[p["image"]]
            assert rec["image_sha256"] == p["sha256"] and rec["colourway"] == "variant", p
            assert rec["fit_status"].startswith(f"camera transported from {src}") and rec["matte_mode"] == "supplied"
            if Path(rec["image"]).is_file():
                assert sha(rec["image"]) == p["sha256"]


def test_prepare_refusals(tmp_path):
    src = sources(tmp_path)
    kw = dict(source_scene=src["scene"], reference=src["reference"], basis_json=src["basis_json"],
              lab_class=PrepLab, import_basis=fake_import, colourway_photos=src["colourway_photos"],
              reference_photos=src["reference_photos"], photo_ids=PRADA.PHOTO_IDS,
                      camera_conflicts=PRADA.CAMERA_CONFLICTS, reading_precision=PRADA.READING_PRECISION)
    with pytest.raises(ValueError, match="expected sha256"):
        cli.prepare(tmp_path / "a", scene_sha256="0" * 64, **kw)
    with pytest.raises(ValueError, match="rig"):
        cli.prepare(tmp_path / "b", rig="photo_v1", scene_sha256=src["scene_sha256"], **kw)
    (tmp_path / "c").mkdir()
    with pytest.raises(ValueError, match="new empty"):
        cli.prepare(tmp_path / "c", scene_sha256=src["scene_sha256"], **kw)
    extra = reference_only(tmp_path)
    bad = [{**extra, "sha256": "0" * 64}]                          # a reference photo's bytes are not the pinned ones
    with pytest.raises(ValueError, match="expected sha256"):
        cli.prepare(tmp_path / "e", scene_sha256=src["scene_sha256"], **{**kw, "reference_photos": bad})
    for n, want in enumerate(({"id": "B1", "image": "reference-05.png", "colour": "black",   # a target photo
                               "sha256": sha(src["reference"] / "reference-05.png")},
                              {**src["colourway_photos"][0], "sha256": "0" * 64},
                              {**src["colourway_photos"][1], "sha256": "0" * 64})):  # B2's bytes are not the pinned
        with pytest.raises(ValueError, match="not a colourway image"):
            cli.prepare(tmp_path / f"f{n}", scene_sha256=src["scene_sha256"], **{**kw, "colourway_photos": (want,)})
    with pytest.raises(ValueError, match="distinct"):                 # a reference photo reusing a colourway id
        cli.prepare(tmp_path / "g", scene_sha256=src["scene_sha256"],
                    **{**kw, "reference_photos": ({**extra, "id": "B2"},)})
    for n, name in enumerate(("black-side.png", "black-angled.png")):
        matte = tmp_path / "mattes" / name
        saved = matte.read_bytes()
        with Image.open(matte) as im:
            size = im.size
        Image.new("L", size, 0).save(matte)
        with pytest.raises(ValueError, match="supplied matte"):
            cli.prepare(tmp_path / f"h{n}", scene_sha256=src["scene_sha256"], **kw)
        matte.write_bytes(saved)
    doc = json.loads(src["basis_json"].read_text(encoding="utf-8"))
    doc["images"]["reference-03.png"]["image_sha256"] = "f" * 64
    src["basis_json"].write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ValueError, match="manifest and the basis disagree"):
        cli.prepare(tmp_path / "d", scene_sha256=src["scene_sha256"], **kw)


# --------------------------------------------------------------------------------------------- make_spec
@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"], ids=["lf", "crlf", "cr"])
def test_make_spec_preserves_exact_prepared_brief_newlines(tmp_path, newline):
    """The stored brief is immutable input text, including its original newline bytes."""
    root, _ = package(tmp_path)
    path = root / "inputs/brief.txt"
    canonical = path.read_bytes().decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    exact = canonical.replace("\n", newline)
    path.write_bytes(exact.encode("utf-8"))
    # Prepare a coherent test package with this encoding; do not bypass input verification.
    manifest_path = root / "inputs/lab.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    next(row for row in manifest["assets"] if row["path"] == "brief.txt")["sha256"] = sha(path)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    lab = lab_on(root)
    settings = T.run_settings(effort="high", max_turns=40)
    spec, _ = cli.make_spec(root, "exact-brief", settings, "20", write_request=True, lab=lab,
                            estimate={"text": "offline test estimate"})
    assert spec.brief == exact
    assert path.read_bytes() == spec.brief.encode("utf-8")
    run = root / "runs/exact-brief"
    request = json.loads((run / "approval-request.json").read_text(encoding="utf-8"))["request"]
    assert request["run_inputs"]["brief_sha256"] == sha(path)
    assert A.canonical_json(L._check_bound_request(run, spec)["run_inputs"]) == A.canonical_json(request["run_inputs"])
    assert not (run / "approval.json").exists() and not A.is_consumed(run)
    if newline != "\n":
        with pytest.raises(A.ApprovalRefused, match="brief differs"):
            A.run_input_binding(root, canonical, spec.photos)


def test_make_spec_binds_the_facet_settings_and_writes_an_unapproved_request(tmp_path):
    root, _ = package(tmp_path)
    lab = lab_on(root)
    settings = T.run_settings(effort="max", max_turns=40, finish_k=5)
    spec, lab = cli.make_spec(root, "facet-001", settings, "20", write_request=True, lab=lab,
                              estimate={"text": "test estimate"})
    run = root / "runs" / "facet-001"
    doc = json.loads((run / "approval-request.json").read_text(encoding="utf-8"))
    req = doc["request"]
    assert not (run / "approval.json").exists() and not A.is_consumed(run)
    assert req["settings"] == settings.approval_settings() and req["settings"]["tool_choice"] == "required"
    assert req["settings"]["reset_tool_choice"] is False and req["settings"]["finish"]["k"] == 5
    assert req["settings"]["context_guard"]["input_tokens"] == T.CONTEXT_GUARD_TOKENS
    assert req["tool_registry_sha256"] == L.tool_registry_sha256(spec.tools, settings) != \
        L.tool_registry_sha256(spec.tools)
    assert doc["request_sha256"] == spec.current_request_sha256 and doc["estimate"] == {"text": "test estimate"}
    sent = (run / "instructions-sent.txt").read_text(encoding="utf-8")
    assert sent == spec.instructions and "at least 5 distinct applied heads" in sent.lower() and cli.K_TOKEN not in sent
    assert req["instructions_sha256"] == L.instructions_sha256(sent)
    raw = (run / "instructions-sent.txt").read_bytes()                  # the exact bytes sent: LF, on every platform
    assert b"\r" not in raw and hashlib.sha256(raw).hexdigest() == req["instructions_sha256"]
    assert [p.label for p in spec.photos] == [f"TARGET PHOTO 0{k} ({k} of 6)" for k in range(1, 7)] + [
        "BLACK PHOTO B1 (1 of 3; the same model in glossy black, calibrated camera)",
        "BLACK PHOTO B2 (2 of 3; the same model in glossy black, calibrated camera)",
        "BLACK PHOTO B3 (3 of 3; the same model in glossy black, calibrated camera)"]
    assert [r["path"] for r in req["run_inputs"]["photos"]] == [f"images/reference-0{k}.png" for k in range(1, 7)] + [
        "images/black-side.png", "images/black-angled.png", "images/black-front.png"]     # every image, sent first
    assert [p.view for p in spec.photos][-3:] == ["side", "angled", "front"]
    assert spec.contract.named == frozenset(T.TOOL_NAMES) and "c0001" in spec.scene_note
    # the loop's own re-check of the bound request agrees (settings included)
    assert L._check_bound_request(run, spec)["tool_registry_sha256"] == req["tool_registry_sha256"]


def test_make_spec_refuses_drift_and_a_used_package(tmp_path, monkeypatch):
    root, _ = package(tmp_path, rig="photo_env")
    settings = T.run_settings(effort="high", max_turns=40)

    def spec_of(lab):
        return cli.make_spec(root, "r", settings, "10", lab=lab)
    lab = lab_on(root)
    assert spec_of(lab)[0].settings.finish_tool == "facet_finish"
    monkeypatch.setattr(cli, "implementation", lambda: {})
    with pytest.raises(ValueError, match="implementation changed"):
        spec_of(lab)
    monkeypatch.undo()
    env = root / "work" / "compare" / "env-basis-01.json"
    env.write_text(json.dumps({"weights_sha": "other"}), encoding="utf-8")
    with pytest.raises(ValueError, match="photo_env light changed"):
        spec_of(lab)
    env.write_text(json.dumps({"weights_sha": "w" * 16}), encoding="utf-8")
    spec_of(lab)
    basis = root / "cameras" / "basis-01" / "basis.json"
    saved = basis.read_bytes()
    basis.write_bytes(saved + b" ")
    with pytest.raises(ValueError, match="pose basis changed"):
        spec_of(lab)
    basis.write_bytes(saved)
    for name in ("black-side.png", "black-angled.png"):            # each black photo's supplied matte is bound too
        matte = root / "basis" / "mattes" / name
        saved = matte.read_bytes()
        matte.write_bytes(saved + b" ")
        with pytest.raises(ValueError, match="pose basis changed"):
            spec_of(lab)
        matte.write_bytes(saved)
    for name in ("reference-02.png", "black-front.png"):
        photo = root / "inputs" / "images" / name
        saved = photo.read_bytes()
        photo.write_bytes(saved + b"x")
        with pytest.raises(ValueError, match="input bytes changed"):
            spec_of(lab)
        photo.write_bytes(saved)
    lab._compare("call-1", ["05"], by="astra")                        # a facet tool ran: not clean any more
    with pytest.raises(ValueError, match="clean package"):
        spec_of(lab)


# --------------------------------------------------------------------------------------------- instructions
# WP6_V3 §6: the prompt is the coordinator's (it teaches the object and an order of reasoning); this file pins only the
# v3 tool names, the {K} token, the absence of job facts and of v2's retired phrases, the length cap, and that the
# FINISHING paragraph says what state.finish_missing checks. v2 pinned 96 phrases here (FINDINGS §0.6: "the prompt
# reached 13,974 of a 14,000-character cap, with 96 phrases pinned by a test"); those pins are retired with v2.
V3_TOOLS = ("facet_inspect", "photo_image", "photo_read", "photo_compare", "facet_edit", "facet_variants",
            "facet_finish", "read_report", "facet_crease_compare", "facet_visible_angle")
INSTRUCTIONS_MAX = 19_000                       # ASSEMBLY M6 6d: focus budget, not a provider limit.
# Product words of the Prada job (the brief carries the job's facts; its face labels in the v2 runs: crown, logo,
# nose, shoulder, lower, tail; its materials and markings) and the colours of its photos.
PRODUCT_WORDS = ("prada", "acetate", "logo", "crown", "nose", "shoulder", "milano", "hallmark", "cream",
                 "black", "tortoise", "havana", "pr 17ws", "prs 17ws")
PHOTO_ID_RE = re.compile(r"(?<![A-Za-z0-9_.])(?:0[1-9]|B[1-9])(?![A-Za-z0-9_])")


def instructions_raw() -> str:
    return (cli.HERE / "instructions.txt").read_text(encoding="utf-8")


def finish_clauses(text: str) -> dict:
    """The workflow's finish preconditions, independent of line wrapping."""
    fin = text.split("Its procedural requirements are:", 1)[1].split("These conditions", 1)[0]
    parts = re.split(r"(?m)^(\d)\. ", fin)
    return {int(n): " ".join(clause.split()) for n, clause in zip(parts[1::2], parts[2::2])}


def test_instructions_name_every_v3_tool_once_with_k_and_fit_the_cap(tmp_path):
    """Every v3 tool is named (as loop.contract_problems finds a name), the run's K replaces the one {K} token, and the
    prompt stays within ASSEMBLY M6 6d's 19,000-character focus budget."""
    raw = instructions_raw()
    for name in V3_TOOLS:
        assert re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", raw), name
    assert "facet_restore" not in raw
    assert raw.count(cli.K_TOKEN) == 1
    (tmp_path / "inputs").mkdir()
    (tmp_path / "inputs" / "instructions.txt").write_text(raw, encoding="utf-8")
    text = cli.instructions_text(tmp_path, 4)
    assert cli.K_TOKEN not in text and "at least 4 distinct applied heads" in text.lower()
    (tmp_path / "inputs" / "instructions.txt").write_text(raw.replace(cli.K_TOKEN, "4"), encoding="utf-8")
    with pytest.raises(ValueError, match="once"):
        cli.instructions_text(tmp_path, 4)
    assert len(raw) <= INSTRUCTIONS_MAX, len(raw)


def test_instructions_carry_no_job_facts():
    """No photo id of a job (01..09, B1..B9) and no product word: the job's facts are the brief's (cli.brief_text)."""
    raw = instructions_raw()
    assert PHOTO_ID_RE.findall(raw) == []
    low = raw.lower()
    for word in PRODUCT_WORDS:
        assert not re.search(rf"(?<![a-z]){re.escape(word)}", low), word


def test_visible_angle_policy_is_unresolved_only_and_carries_visual_evidence():
    text = " ".join(instructions_raw().split())
    assert "Unresolved does not mean flat" in text
    assert "Only unresolved measurements permit a visible-angle declaration" in text
    assert "overrides unresolved local readings while retaining residuals" in text
    assert "certain contradictory measurements still refuse" in text
    assert "closed-solid, thickness, declared-shape and strict-spec failures" in text
    assert "named facet or outline mismatch with its photo/crop reference" in text
    assert "carry this evidence into its whole-product finish review" in text


def test_instructions_carry_no_retired_phrase():
    """None of v2's removed tools and rules (cli.RETIRED_PHRASES: fades, crease+rise planes, the solve option that
    "moves least", "do not redeclare", photo_compare's depths parameter, the top / rear outer-edge offset tables, the
    "start" column and the outline line, W_CONDITIONING, lean_deg, the section strip, crease margins, facet_restore:
    WP6_V3 §6 "removed from Astra's view")."""
    low = " ".join(instructions_raw().split()).lower()
    assert set(cli.RETIRED_PHRASES) >= {"fade", "rise_per_mm", "crease plane", "moves least", "do not redeclare",
                                         "photo_compare's depths", "depths read", "with depths", "`depths`",
                                         "outer-edge offset", "outline line", "offset table", "start column",
                                         "\"start\" column", "facet_restore", "margins", "w_conditioning", "lean_deg",
                                         "section strip"}
    for phrase in cli.RETIRED_PHRASES:
        assert phrase.lower() not in low, phrase
    assert cli.tool_contract().removed_phrases == tuple(L.REMOVED_PHRASES) + cli.RETIRED_PHRASES


def test_instructions_carry_no_removed_phrase():
    """None of loop.REMOVED_PHRASES (the loop's own list, checked by make_spec's contract since v2: "hypothes" among
    them), so make_spec can build a request from the instructions."""
    low = " ".join(instructions_raw().split()).lower()
    for phrase in L.REMOVED_PHRASES:
        assert phrase.lower() not in low, phrase


def test_retired_phrases_ban_the_depths_parameter_not_the_word():
    """The retired depths item is photo_compare's v2 parameter: its forms in v2's prompt, brief and tool text are
    banned ("Measure t with photo_compare's depths", "depths reads t", "measured with depths", `depths`), the plain
    English plural is not (review 2026-10-12, P3: "two vertices at different depths" blocked every package); the
    other names WP6_V3 §6 removes from Astra's view are banned (W_CONDITIONING, lean_deg, the section strip, the
    "start" column, the 03/06 offset tables)."""
    def hits(text):
        return [p for p in cli.RETIRED_PHRASES if p.lower() in text.lower()]
    assert hits("One corner in a side view can be two vertices at different depths") == []
    assert hits("faces at several depths; a depth error shifts it") == []
    for v2 in ("Measure t with photo_compare's depths in the moderate-angle views",
               "depths reads t at about 3.1 px per mm", "a corner measured with depths", "pass `depths` for t",
               "W_CONDITIONING: the fit is ill-conditioned", "lean_deg 1.3", "The section strip shows what the "
               "model built", "the \"start\" column", "the start column", "the 03/06 offset tables"):
        assert hits(v2), v2


def test_instructions_finish_text_is_what_finish_missing_checks():
    """The finish paragraph states the preconditions state.finish_missing returns (1-4 for any finish, 5 for stuck
    only), in the state's own terms: K and the one head-distinctness rule (state.DIFFERENT_ATTEMPT), the variants
    spread (geometry.VARIANT_DISTINCT_MM), best_checkpoint compared at every photo with a camera (the
    ``camera_photos`` it is given: a photo of another colour counts), the object note, and stuck's last STUCK_LAST
    judgements on STUCK_LAST distinct heads with none "better" and a variants call after the latest applied head.
    The variants result must be shown; it has no compare_id and cannot receive a compare judgement."""
    from glasses3d.agent.temple_lab import geometry as G
    from glasses3d.agent.temple_lab import state as S
    pre = finish_clauses(instructions_raw())
    assert sorted(pre) == [1, 2, 3, 4, 5]
    st = S.new_state(cli.DEFAULT_RIG)
    kw = dict(best_revision=0, k=4, distinct=lambda a, b: a != b, camera_photos=["01", "B1"], has_object=False)
    assert [m["precondition"] for m in S.finish_missing(st, status="matched", **kw)] == [1, 2, 3, 4]
    assert [m["precondition"] for m in S.finish_missing(st, status="stuck", **kw)] == [1, 2, 3, 4, 5]
    assert cli.K_TOKEN in pre[1] and "distinct applied heads" in pre[1] and "compared in full" in pre[1]
    assert "built surface or outline" in pre[1] and f"at least {G.HEAD_DISTINCT_MM:g} mm" in pre[1]
    assert "every other counted head" in pre[1]
    assert "facet_variants" in pre[2] and "at least three variants pairwise" in pre[2] and \
        f"at least {G.VARIANT_DISTINCT_MM:g} mm apart" in pre[2]
    assert f"at least {G.HEAD_DISTINCT_MM:g} mm" in S.DIFFERENT_ATTEMPT
    assert "best revision" in pre[3] and "every photo with a camera" in pre[3] and "full comparison" in pre[3]
    assert "object note" in pre[4]
    assert pre[5].lstrip().startswith("For stuck") and "variants result with a rendered alternative was shown" in pre[5]
    assert "after the latest applied head" in pre[5]
    assert S.STUCK_LAST == 3 and "three judgements cover three distinct heads with none rated better" in pre[5]
    assert "A refusal names unmet conditions or validation failure" in instructions_raw()
    # behind the words: a compare of best_checkpoint's revision at 01 only leaves the photo of another colour (B1)
    # missing; an object note clears 4
    st["compares"].append({"compare_id": "cmp001", "checkpoint": "c1", "fingerprint": "f1", "revision": 0,
                           "photos": ["01"], "by": "astra", "call_id": "x", "report": {}, "seq": 1, "judged": True})
    missing = S.finish_missing(st, status="matched", **{**kw, "has_object": True})
    assert [m["precondition"] for m in missing] == [1, 2, 3]
    assert next(m for m in missing if m["precondition"] == 3)["photos"] == ["B1"]


def test_instructions_contract_with_the_registry(tmp_path):
    """loop.contract_problems with the run's contract (cli.tool_contract) on the instructions, a package's brief and
    the lab's registry: the instructions name exactly the registered tools (the v3 set) and no retired or removed
    phrase appears anywhere Astra reads."""
    assert set(T.TOOL_NAMES) == set(V3_TOOLS)
    root, _ = package(tmp_path / "p")
    tools = T.registry(lab_on(root), T.run_settings(effort="max", finish_k=4))
    (tmp_path / "inputs").mkdir()
    (tmp_path / "inputs" / "instructions.txt").write_text(instructions_raw(), encoding="utf-8")
    text = cli.instructions_text(tmp_path, 4)
    brief = (root / "inputs" / "brief.txt").read_text(encoding="utf-8")
    assert L.contract_problems(cli.tool_contract(), instructions=text, brief=brief, tools=tools) == []


# --------------------------------------------------------------------------------------------- brief
def test_brief_geometry_helpers():
    """The brief's photo facts from cameras: the angle to the outer face (0 square-on, 90 edge-on), temple-relative
    directions (a mirrored side view is the same direction), the bands, the camera lineage and fit texts."""
    side, mirrored = {"yaw": 90.0, "pitch": 0.0}, {"yaw": -90.0, "pitch": 0.0}
    angled, front, top = {"yaw": 30.0, "pitch": 0.0}, {"yaw": 0.0, "pitch": 0.0}, {"yaw": 0.0, "pitch": 60.0}
    assert cli.outer_face_deg(side) == pytest.approx(0.0, abs=1e-6)
    assert cli.outer_face_deg(mirrored) == pytest.approx(0.0, abs=1e-6)
    assert cli.outer_face_deg(angled) == pytest.approx(60.0) and cli.outer_face_deg(front) == pytest.approx(90.0)
    assert cli.outer_face_deg(top) == pytest.approx(90.0)
    assert cli.direction_deg(side, mirrored) == pytest.approx(0.0, abs=1e-6)          # the mirror counts as one
    assert cli.direction_deg(front, top) == pytest.approx(60.0)
    assert cli.direction_groups({"a": mirrored, "b": angled, "c": side, "d": {"yaw": -30.0, "pitch": 5.0},
                                 "e": top, "f": front}) == [["a", "c"], ["b", "d"], ["e"], ["f"]]
    assert [cli.angle_band(a) for a in (0.0, 29.9, 30.0, 74.9, 75.0, 90.0)] == [
        "square-on", "square-on", "at a moderate angle", "at a moderate angle", "edge-on", "edge-on"]
    assert cli.temple_seen(mirrored) == "the mirrored temple, from its own side" and cli.temple_seen(side) == \
        "the charted temple" and cli.side_note({"camera": side}) == "it shows the charted temple"
    assert cli.looks_along_deg({"yaw": 0.0, "pitch": 10.0}) == pytest.approx(10.0)
    assert cli.looks_along_deg({"yaw": 180.0, "pitch": 0.0}) == pytest.approx(0.0, abs=1e-6)
    assert cli.looks_along_deg({"yaw": 90.0, "pitch": 0.0}) == pytest.approx(90.0)
    assert cli._deg(-0.02) == "+0.0" and cli._deg(-90.81) == "-90.8"
    ids = {"reference-02.webp": "02", "reference-05.png": "05"}
    moved = {"transport": {"source": "C:/x/reference-02.webp", "mask_iou_after_warp": 0.99243,
                           "mean_bidirectional_px": 0.8233}, "theta_observed": {"+1": False, "-1": False},
             "theta_deg": {"+1": 0, "-1": 0}, "hinge_axis_xz_mm": {"+1": [60, 0], "-1": [-60, 0]},
             "fit_status": "camera transported from 02 by image similarity (contour 0.82 px, IoU 0.992); theta "
                           "copied from 02"}
    assert cli.camera_fit_text(moved, ids) == ("camera moved from 02's by matching the two photos' silhouettes (photo "
                                               "to photo, on its registration matte: IoU 0.992, mean contour 0.8 px), "
                                               "the temples' hinge opening as in 02; not checked against the model")
    checked = {**moved, "model_check": {"iou": 0.93418, "temple_iou": 0.87538,
                                        "against": "original model, white-background matte"}}
    assert cli.camera_fit_text(checked, ids) == ("camera moved from 02's by matching the two photos' silhouettes "
                                                 "(photo to photo, on its registration matte: IoU 0.992, mean contour "
                                                 "0.8 px), the temples' hinge opening as in 02; starting model's "
                                                 "silhouette on the compare's raster: IoU 0.934, temple IoU 0.875")
    # B1's record (tests/proofs/wp6v2_basis.py): no transport block, its source in fit_status, and the WP6 v1 all-views
    # validation's IoU at the transported camera; it is a moved camera, never "fitted" (review 2026-10-12), and its
    # check is labelled as that validation's, the one the target photos' fits print, never as the starting model's
    # check B2 and B3 print (review 2026-10-12, P3)
    b1 = {"fit_status": "camera transported from 05", "iou": 0.88325, "contour_mean_px": 7.2318,
          "theta_deg": {"+1": 0, "-1": 0}, "hinge_axis_xz_mm": {"+1": [60, 0], "-1": [-60, 0]},
          "theta_observed": {"+1": False, "-1": False}}
    assert cli.camera_fit_text(b1, ids) == ("camera moved from 05's by matching the two photos' silhouettes, the "
                                            "temples' hinge opening as in 05; the model's silhouette at this camera "
                                            "in the all-views validation that fitted the target photos' cameras: IoU "
                                            "0.883, mean contour 7.2 px")
    assert "starting model" not in cli.camera_fit_text(b1, ids)
    assert "starting model's silhouette" in cli.camera_fit_text({**b1, "model_check": {"iou": 0.8467,
                                                                                       "temple_iou": 0.8591}}, ids)
    assert cli.camera_fit_text({"iou": 0.87317, "contour_mean_px": 8.07}, ids) == \
        "camera fitted to the model's silhouette (all-views fit): IoU 0.873, mean contour 8.1 px"
    assert cli.camera_fit_text({}, ids) is None
    assert [cli.camera_source(r, ids) for r in (moved, b1, {"fit_status": "fitted (all_views_validation: iou 0.9)"},
                                                {"fit_status": "camera transported from reference-05.png"},
                                                {"fit_status": "camera transported from 09"}, None)] == \
        ["02", "05", None, "05", "a photo not in this job", None]
    assert "hinge opening" not in cli.camera_fit_text({**b1, "theta_observed": {"+1": True}}, ids)   # not copied
    assert cli.SIDE_VIEWS is seeing.SIDE_VIEWS                     # one list: the views whose compares print outlines
    assert cli.colour_text({"colour": "black", "finish": "glossy"}) == "glossy black"
    assert cli.colour_text({}) == "another colour"


@pytest.mark.parametrize("pose", [{}, {"theta_observed": {"+1": False, "-1": False}},
                                 {"theta_deg": {"+1": 0}, "hinge_axis_xz_mm": {"+1": [60, 0]}}])
def test_transported_camera_does_not_invent_copied_opening(pose):
    """Camera transport alone supplies no evidence of a copied calibrated hinge pose."""
    ids = {"side.png": "side", "variant.png": "variant"}
    moved = {"camera": {"yaw": 90}, "transport": {"source": "side.png"}, **pose}
    assert "hinge opening" not in cli.camera_fit_text(moved, ids)
    text = cli.lineage_text({"side": {"camera": {"yaw": 90}}, "variant": moved}, ids)
    assert "shares that camera's error" in text and "theta" not in text and "hinge opening" not in text


def test_brief_mixed_pose_records_name_only_the_calibrated_photos():
    photos = [{"id": name, "path": f"images/{name}.png", "view": "side", "role": "target"}
              for name in ("posed", "authored")]
    camera = {"camera": {"yaw": 90}}
    cameras = {"posed.png": {**camera, "theta_deg": {"+1": 0}, "hinge_axis_xz_mm": {"+1": [60, 0]}},
               "authored.png": {**camera, "transport": {"source": "posed.png"}}}
    brief = cli.brief_text("A different frame", photos, cameras)
    assert "Calibrated hinge-opening metadata: posed. For these photos, the camera fit includes" in brief
    assert "As-authored temple pose; hinge opening is not calibrated for: authored." in brief
    assert "Each camera fit" not in brief and "hinge opening as in" not in brief and "theta) copied" not in brief


def test_verified_rigid_identity_is_disclosed_without_a_calibration_claim():
    photos = [{"id": "authored", "path": "images/authored.png", "view": "side", "role": "target"}]
    cameras = {"authored.png": {"camera": {"yaw": 90}, "theta_deg": {"+1": 0, "-1": 0},
        "hinge_axis_xz_mm": {"+1": [62, -10], "-1": [-62, -10]},
        "theta_observed": {"+1": False, "-1": False},
        "pose_provenance": {"mode": "as_authored", "opening_calibrated": False}}}
    brief = cli.brief_text("An authored frame", photos, cameras)
    assert "Calibrated hinge-opening metadata" not in brief and "For these photos, the camera fit includes" not in brief
    assert "As-authored temple pose; hinge opening is not calibrated for: authored." in brief
    assert "verified rigid camera fit used the authored mesh with zero additional hinge rotation" in brief
    assert "not a measured photo opening" in brief and "axes come from the captured current model" in brief
    assert "not a measured opening confidence" in brief


def test_brief_outline_offsets_by_view():
    """A side view's compares print the outline offsets of the temple whose outer face it sees (seeing._side_outline):
    looking down on the temple (01 / 05 / B1 from 13.6-13.7 deg above) the bottom outline and, for the top, the top
    face's far edge minus its median (seeing._far_edge_text, v3p3-fix-seeing: "not its top" hid the arm's top 2-4 mm
    low behind d 100, which seeing now prints); looking up the other way round; level both outlines; no other view
    prints numbers (v3: no top / rear outer-edge offsets, no outline line)."""
    assert cli.outline_text("side", {"yaw": 90.6, "pitch": 13.6}) == (
        "its compares print the charted temple's bottom outline offsets and, for its top, the top face's far edge "
        "minus its median (the view looks down on the top face)")
    assert cli.outline_text("side", {"yaw": -90.8, "pitch": 13.7}).startswith(
        "its compares print the mirrored temple's bottom outline offsets")
    assert cli.outline_text("side", {"yaw": 90.0, "pitch": -5.0}) == (
        "its compares print the charted temple's top outline offsets and, for its bottom, the bottom face's far edge "
        "minus its median (the view looks up at the bottom face)")
    assert cli.outline_text("side", {"yaw": 90.0, "pitch": 0.5}) == \
        "its compares print the charted temple's top and bottom outline offsets"
    for view, cam in (("angled", {"yaw": 30.0, "pitch": 11.0}), ("top", {"yaw": 0.0, "pitch": 60.0}),
                      ("rear", {"yaw": 180.0, "pitch": 9.0}), ("front", {"yaw": 0.0, "pitch": 10.0})):
        assert cli.outline_text(view, cam) is None, view


def test_brief_lineage_precision_theta_and_conflict_texts():
    """The v3 brief lines that are not per photo: which cameras were fitted on their own and which were moved from
    another photo's with its theta (lineage_text), how precisely photo_read reads which kind of point (the job's
    READING_PRECISION), the theta lever (readings.THETA_SD_DEG; d x pi / 180 mm per degree) and a camera-model
    prior comparison worded as a measured difference, never as a proved camera limit or doubt about a photo."""
    from glasses3d.agent.temple_lab import readings as RD
    ids = {"reference-05.png": "05", "reference-02.png": "02", "black-side.png": "B1"}
    recs = {"05": {"camera": {"yaw": 90.0}}, "02": {"camera": {"yaw": 30.0}},
            "B1": {"camera": {"yaw": 90.0}, "fit_status": "camera transported from 05",
                   "theta_deg": {"+1": 0, "-1": 0}, "hinge_axis_xz_mm": {"+1": [60, 0], "-1": [-60, 0]},
                   "theta_observed": {"+1": False, "-1": False}}, "R1": None}
    assert cli.lineage_text(recs, ids) == (
        "Cameras: 05 and 02 each have a camera fitted on its own. B1's camera was not fitted: it is 05's camera moved "
        "onto its photo by an image similarity that matches the two photos' silhouettes, with the temples' hinge "
        "opening (theta) copied, so it shares that camera's error and theta. Photos of one direction (above) read a "
        "point only together with a photo of another direction.")
    assert cli.lineage_text({"05": {"camera": {"yaw": 90.0}}}, ids) == \
        "Cameras: 05 has a camera fitted on its own."
    assert cli.lineage_text({"R1": None}, ids) is None
    assert cli.precision_text(None) is None
    p = cli.precision_text(PRADA.READING_PRECISION)
    assert p.startswith("Readings (photo_read) on these photos: a crisp corner read from two directions is good to "
                        "about 0.2-0.5 mm; a tip, or a point on an edge that runs along the line of sight between the "
                        "photos, only to about 0.7-1.5 mm along that edge, even where a single pick of it prints less")
    assert "Read as a crossing of two traced edges" in p and "smallest where the edges cross steeply" in p
    # a back corner's absolute X carries the cameras' theta beside its pick error (review 2026-10-12, P2)
    assert p.endswith("These are the picks' errors: a point far back from the hinge also carries the cameras' hinge "
                      "opening in its absolute X (below).")
    th = cli.theta_text()
    assert f"taken as uncertain by {RD.THETA_SD_DEG:g} deg" in th
    assert "X moves about 0.4 mm per degree at d 25, 0.9 at d 50, 1.4 at d 80" in th      # d x pi / 180
    assert "turns the temple about its hinge as one rigid piece" in th
    # one rigid turn only for readings of the same photos: readings.py moves each camera fit's theta separately and
    # adds the fits in quadrature (review 2026-10-12, P3: "faces stay flat and edges straight" overclaimed)
    assert th.endswith("the same for every reading made from the same photos, so such readings keep a flat face flat; "
                       "readings from different photos carry their own theta.")
    assert "faces stay flat" not in th and "edges straight" not in th
    assert [round(d * math.pi / 180.0, 1) for d in (75, 85)] == [1.3, 1.5]            # WP6_V3 §0.3: ~1.3 at d 75-85
    c = cli.conflict_text(PRADA.CAMERA_CONFLICTS[0])
    assert c == ("Supplied prior comparison: the outer outline that photo 03 shows at d 10-40 and the other photos "
                 "had a reported difference there of up to about 2.2 mm, 03's further out, on both temples, under "
                 "that comparison's calibration. Check the current camera/opening fit and reading residuals before "
                 "drawing a geometry conclusion; this observation alone does not establish an irreconcilable "
                 "camera model.")
    back = cli.conflict_text(PRADA.CAMERA_CONFLICTS[1])
    assert back == ("Supplied prior comparison: the outer outline that photos 03 and 06 show near d 75 and the "
                    "two-direction readings of the side and angled photos had a reported difference there of up to "
                    "about 2.7 mm, the readings further out, on the charted temple (the mirrored temple was not "
                    "checked), under that comparison's calibration. Check the current camera/opening fit and "
                    "reading residuals before drawing a geometry conclusion; this observation alone does not "
                    "establish an irreconcilable camera model.")
    for text in (c, back):
        for doubt in ("wrong", "error", "inconsistent", "unreliable", "bad", "fault", "distort", "trust", "ignore",
                      "suspect", "not real"):
            assert doubt not in text.lower(), doubt


REVIEW = cli.G3D / "data/wp6v2/review_20261012"


def test_camera_conflicts_and_reading_precision_are_job_data():
    """CAMERA_CONFLICTS and READING_PRECISION are the Prada job's data beside COLOURWAY_PHOTOS (prepare passes them to
    the brief; a brief without them has neither line). Where the investigation's evidence is present (git-ignored
    data), the rows print what it measured (review 2026-10-12, P3: "up to about 2 mm" against +2.18, "1 mm or more"
    against 0.7-1.5 mm; P2: the back-of-block conflict of 03 and 06 with the readings was missing)."""
    assert [(tuple(c["photos"]), c["d_mm"], c["up_to_mm"], c["temples"], c["further_out"])
            for c in PRADA.CAMERA_CONFLICTS] == [
        (("03",), (10, 40), 2.2, "both temples", "03's further out"),
        (("03", "06"), 75, 2.7, "the charted temple (the mirrored temple was not checked)",
         "the readings further out")]
    assert tuple(PRADA.READING_PRECISION["corner_mm"]) == (0.2, 0.5)
    assert tuple(PRADA.READING_PRECISION["along_mm"]) == (0.7, 1.5)
    # FINDINGS §0.5 (crisp corners) and §1 (tips); its §0.3 is photo 03's conflict, not the precision
    assert PRADA.READING_PRECISION["evidence"].startswith("data/wp6v2/review_20261012/FINDINGS.md §0.5 and §1;")
    import inspect
    defaults = {k: v.default for k, v in inspect.signature(cli.prepare).parameters.items()}
    assert defaults["camera_conflicts"] == () and defaults["reading_precision"] is None
    photos = [{"id": "01", "path": "images/reference-01.png", "view": "side", "role": "target"}]
    plain = cli.brief_text("Frame X", photos, {"reference-01.png": {"camera": {"yaw": 90.0, "pitch": 5.0}}})
    assert "Supplied prior comparison" not in plain and "Readings (photo_read)" not in plain
    assert "As-authored temple pose; hinge opening is not calibrated for: 01." in plain
    assert "Each camera fit includes the temples' hinge opening (theta)" not in plain
    mid = cli.G3D / PRADA.CAMERA_CONFLICTS[0]["evidence"]
    if mid.is_file():
        text = mid.read_text(encoding="utf-8")
        assert "photo 03's front-block outline cannot be matched by any shape the other eight photos allow" in text
        row = next(line for line in text.splitlines() if line.lstrip().startswith("| excess, temple +1 / −1 (mm)"))
        bins = [[float(v) for v in re.findall(r"[+−-]\d+\.\d+", cell.replace("−", "-"))]
                for cell in row.split("|")[2:-1]]
        assert len(bins) == 9                                                     # d 5-10 .. 45-48
        assert round(max(max(b) for b in bins), 1) == PRADA.CAMERA_CONFLICTS[0]["up_to_mm"]      # +2.18 at d 20-25
        assert all(min(b) > 0 for b in bins[1:7]) and max(bins[0]) < 0.3 and max(bins[7]) < 0   # d 10-40 only
    theta_log = REVIEW / "w2/crown-geometry/theta_both.log"
    if theta_log.is_file():                          # the tip read at each angled-photo theta (crown-geometry §2)
        rows = {}
        for line in theta_log.read_text(encoding="utf-8").splitlines():
            m = re.match(r"theta ([+-]\d+): .*E d ([\d.]+) t ([\d.]+) .*E in 06 u ([\d.]+) \(silhouette ~([\d.]+)\), "
                         r"in 03 u ([\d.]+) \(silhouette ~([\d.]+)\)", line)
            if m:
                rows[int(m.group(1))] = [float(v) for v in m.groups()[1:]]
        fitted, on = rows[0], rows[2]               # the fitted cameras; +2 deg: the tip on both silhouettes
        assert fitted[2] < fitted[3] and fitted[4] > fitted[5]          # outside 06's and 03's outlines at the fit
        assert abs(on[2] - on[3]) <= 4 and abs(on[4] - on[5]) <= 4      # on both at +2 deg (px)
        assert round(fitted[1] - on[1], 1) == PRADA.CAMERA_CONFLICTS[1]["up_to_mm"]                # 7.03 - 4.34
        assert abs(on[0] - PRADA.CAMERA_CONFLICTS[1]["d_mm"]) < 1 > abs(fitted[0] - PRADA.CAMERA_CONFLICTS[1]["d_mm"])
    findings = REVIEW / "FINDINGS.md"
    if findings.is_file():
        text = findings.read_text(encoding="utf-8")
        assert "within 0.23-0.37 mm of the settled values" in text                    # §0.5: crisp corners
        assert "tip d only to ±0.7-1.5 mm" in text                                     # §1: along an edge
        assert "by up to +2.2 mm" in text                                              # §0.3: photo 03


def test_brief_of_a_job_without_another_colour_or_a_moderate_view():
    """A photo set with only target photos and no view at a moderate angle to the outer face: the brief says so
    ("none"), names no other colour and no finish, every camera its own fit."""
    photos = [{"id": "01", "path": "images/reference-01.png", "view": "side", "role": "target"},
              {"id": "02", "path": "images/reference-02.png", "view": "front", "role": "target"},
              {"id": "03", "path": "images/reference-03.png", "view": "rear", "role": "target"}]
    cams = {"reference-01.png": {"camera": {"yaw": 88.0, "pitch": 5.0}},
            "reference-02.png": {"camera": {"yaw": 0.0, "pitch": 5.0}},
            "reference-03.png": {"camera": {"yaw": 180.0, "pitch": 5.0}}}
    brief = cli.brief_text("Frame X", photos, cams)
    assert "At a moderate angle (30 to 75 deg): none." in brief and "two temples): 01 | 02 | 03." in brief
    assert "Outline offsets (photo minus model, mm) are printed for 01 only." in brief
    assert "Front and rear views look along creases that run front to back: 02, 03 (" in brief
    assert "same model in" not in brief and "photos:" not in brief.split("are printed for")[1]
    assert "No finish is given" not in brief                                # no photo row gives a finish
    assert "- 02: front; camera yaw +0.0, pitch +5.0; the outer faces edge-on (90 deg)" in brief
    assert "Cameras: 01, 02 and 03 each have a camera fitted on its own." in brief and "were not fitted" not in brief
    bare = cli.brief_text("Frame X", photos)
    assert bare.count("camera yaw") == 0 and "Cameras:" not in bare and "hinge opening" not in bare   # views only


def test_brief_names_the_light_the_temples_the_scope_and_the_edge_rounding():
    """The facts the instructions point to that are not per photo: the job's fixed render light (every rig the lab
    takes has a description) and what it does not reproduce (tone marks where an edge is, not its size or sign), the
    names the compares give the two temples, revision 0 as it is, the scope (the whole temple to its tip, the outline
    chains editable), and the real temples' edge rounding, "not known" unless the job knows it; finishes are listed
    only for the photo rows that give one."""
    photos = [{"id": "01", "path": "images/reference-01.png", "view": "side", "role": "target"},
              {"id": "B1", "path": "images/black-side.png", "view": "side", "role": "colourway", "colour": "black",
               "finish": "glossy"}]
    assert set(cli.RIG_TEXT) == set(seeing.MATCHED_RIGS)
    for rig in seeing.MATCHED_RIGS:
        brief = cli.brief_text("Frame X", photos, rig=rig)
        assert f"The renders use the job's fixed light, {rig}: {cli.RIG_TEXT[rig]}. The photos' light is " \
               "unknown. " + cli.LIGHT_TEXT in brief
    assert cli.LIGHT_TEXT.startswith("The render light may not reproduce the photos' tones") and \
        cli.LIGHT_TEXT.endswith("in the photos a tone step marks where an edge is, not its size or sign.")
    assert "no light tried" not in cli.LIGHT_TEXT and "unedited frame" not in cli.LIGHT_TEXT
    brief = cli.brief_text("Frame X", photos)
    assert "fixed light, studio_dome_v1:" in brief                        # the default rig, the owner's choice
    assert "The charted temple is the one the spec's d, y and t chart; the mirrored temple is its mirror image" in brief
    assert "compares name the two temples so" in brief
    assert "Glossy photos: B1.\nNo finish is given for 01.\n" in brief
    assert ("The scene holds revision 0, the starting model's temple measured into the spec: its outline (the top "
            "and bottom chains and the tip's rounding) and inner face, and at every d an outer surface straight in y "
            "(the base wedge); no faces, no edges, roll_mm 0. It is not the target.") in brief
    assert ("What you may change is the whole temple from the hinge to its tip: its faces and edges, and its outline "
            "(the outline chains are editable along the whole length).") in brief
    assert "The real temples' edge rounding (roll_mm) is not known: judge it from the photos' edges." in brief
    known = cli.brief_text("Frame X", photos, roll_mm=0.3)
    assert "The real temples' edge rounding is about 0.3 mm (roll_mm)." in known and "not known" not in known
    low = brief.lower()
    for phrase in cli.RETIRED_PHRASES + ("px per mm", "outer-edge", "points_px"):
        assert phrase not in low, phrase


def real_brief() -> str:
    """The brief the package would get from the real pose basis (data/wp6v2/basis/cameras.json, git-ignored), with the
    job's conflicts and reading precision as prepare passes them."""
    doc = json.loads(PRADA.BASIS_JSON.read_text(encoding="utf-8"))
    photos = []
    for name in sorted(n for n, r in doc["images"].items() if r.get("colourway") == "target"):
        rec = doc["images"][name]
        photos.append({"id": Path(name).stem.split("-", 1)[1], "path": f"images/{name}", "view": rec["view_kind"],
                       "role": "target"})
    for w in PRADA.COLOURWAY_PHOTOS:
        photos.append({"id": w["id"], "path": f"images/{w['image']}", "view": doc["images"][w["image"]]["view_kind"],
                       "role": "colourway", "colour": w["colour"], "finish": w["finish"]})
    return cli.brief_text("Prada PRS 17WS 21D5J2 49M", photos, doc["images"], conflicts=PRADA.CAMERA_CONFLICTS,
                          precision=PRADA.READING_PRECISION)


@pytest.mark.skipif(not PRADA.BASIS_JSON.is_file(), reason="the pose basis (git-ignored data) is absent")
def test_brief_on_the_real_pose_basis():
    """The Prada job's brief from its cameras: B1 shares 05's direction and 01 is its mirror, B2 is 02's direction at
    62 deg to the outer face, the edge-on views; each camera's lineage as the basis records it (B1, B2, B3 moved from
    05, 02, 04 with theta copied: B1 is never called an all-views fit, review 2026-10-12, data/wp6v2/basis/report.json
    "camera transported from 05"; its check labelled as the all-views validation's); the side views' bottom outline
    offsets and top face's far edge; the reading precision and theta lever; both camera-model conflicts (03 at the
    front block; 03 and 06 against the readings near d 75); and every pointer the instructions make to the brief. No
    retired line."""
    brief = real_brief()
    assert "two temples): 01, 05, B1 | 02, B2 | 03 | 04, B3 | 06." in brief
    assert "square-on (under 30 deg): 01, 05, B1. At a moderate angle (30 to 75 deg): 02, B2. Edge-on (75 deg or " \
           "more): 03, 04, 06, B3." in brief
    assert "Glossy photos: B1, B2, B3." in brief and "No finish is given for 01, 02, 03, 04, 05, 06." in brief
    assert "- 02: angled; camera yaw +28.2, pitch +11.4; the charted temple's outer face at a moderate angle (62 " \
           "deg); camera fitted to the model's silhouette (all-views fit): IoU 0.926" in brief
    assert "- 01: side; camera yaw -90.8, pitch +13.7; the mirrored temple's outer face, from its own side, " \
           "square-on (14 deg); its compares print the mirrored temple's bottom outline offsets and, for its top, " \
           "the top face's far edge minus its median (the view looks down on the top face)" in brief
    assert "- B1: side, glossy black; camera yaw +90.6, pitch +13.6; the charted temple's outer face square-on (14 " \
           "deg); its compares print the charted temple's bottom outline offsets and, for its top, the top face's " \
           "far edge minus its median (the view looks down on the top face); camera moved from 05's by matching the " \
           "two photos' silhouettes, the temples' hinge " \
           "opening as in 05; the model's silhouette at this camera in the all-views validation that fitted the " \
           "target photos' cameras: IoU 0.883, mean contour 7.2 px" in brief
    assert "camera moved from 02's by matching the two photos' silhouettes (photo to photo, on its registration " \
           "matte: IoU 0.992, mean contour 0.8 px), the temples' hinge opening as in 02; starting model's silhouette " \
           "on the compare's raster: IoU 0.934, temple IoU 0.875" in brief
    assert "camera moved from 04's by matching the two photos' silhouettes (photo to photo, on its registration " \
           "matte: IoU 0.992, mean contour 1.1 px), the temples' hinge opening as in 04; starting model's silhouette " \
           "on the compare's raster: IoU 0.977, temple IoU 0.886" in brief
    rows = {line[2:4]: line for line in brief.splitlines() if line.startswith("- ")}
    assert all("all-views fit" in rows[p] for p in ("01", "02", "03", "04", "05", "06"))
    assert not any("all-views fit" in rows[p] or "fitted to" in rows[p] for p in ("B1", "B2", "B3"))
    assert "not checked against the model" not in brief                  # every moved camera has its check
    assert "Cameras: 01, 02, 03, 04, 05 and 06 each have a camera fitted on its own. The cameras of B1, B2 and B3 " \
           "were not fitted: each is another photo's camera (B1's from 05, B2's from 02 and B3's from 04)" in brief
    doc = json.loads(PRADA.BASIS_JSON.read_text(encoding="utf-8"))
    for p, src in zip(PRADA.COLOURWAY_PHOTOS, ("05", "02", "04")):         # the lineage is the basis's own record
        assert doc["images"][p["image"]]["fit_status"].startswith(f"camera transported from {src}")
        assert not any(doc["images"][p["image"]]["theta_observed"].values())          # theta copied, not observed
    assert "Outline offsets (photo minus model, mm) are printed for 01, 05, B1 only." in brief
    assert not any("its compares print" in rows[p] for p in ("02", "03", "04", "06", "B2", "B3"))
    assert cli.precision_text(PRADA.READING_PRECISION) in brief and cli.theta_text() in brief
    for c in PRADA.CAMERA_CONFLICTS:                    # 03 at the front block; 03 and 06 against the readings at d 75
        assert cli.conflict_text(c) + "\n" in brief
    for pointer in ("charted temple", "mirrored temple", "square-on", "at a moderate angle", "edge-on", "Directions",
                    "look along creases that run front to back", "Glossy photos", "faces that point straight up",
                    "revision 0", "glossy black", "fixed light, studio_dome_v1", "edge rounding", "bottom outline",
                    "Supplied prior comparison", "were not fitted", "What you may change", "a tone step marks where an "
                    "edge is"):
        assert pointer in brief, pointer                               # what the instructions say the brief gives
    low = brief.lower()
    for phrase in cli.RETIRED_PHRASES + tuple(L.REMOVED_PHRASES) + ("px per mm", "outer-edge", "acetate",
                                                                    "far more clearly"):
        assert phrase not in low, phrase


# --------------------------------------------------------------------------------------------- estimate
def sample(tool, chars, images, status="ok", args=300):
    return {"tool": tool, "status": status, "text_chars": chars, "args_chars": args, "images": images}


# A zoom grid as the v3 rehearsal records it (cli.record_kind; the lab registers it as kind "compare" with zoom=True,
# lab.py; seeing: 2 x 2 panels of a 380 x 220 box at k 2, the size of probe 1's real zooms), the same grid as a
# rehearsal recorded it before record_kind (a "compare" image), and pick sheets of a 2- and a 4-photo reading
# (readings.pick_sheet).
ZOOM, ZOOM_AS_COMPARE = [1538, 946, cli.ZOOM_GRID_KIND], [1538, 946, "compare"]
SHEET2, SHEET4 = [972, 728, "reading"], [972, 1412, "reading"]


def rehearsal(scale: int = 1) -> dict:
    cmp_img = [512, 400 * scale, "compare"]
    return {"first_request": {"text_chars": 30_000, "image_sizes": [[1226, 682]] * 6},
            "tool_outputs": [sample("facet_inspect", 1800, [[1024, 454, "chart"], [1022, 576, "sections"]]),
                             sample("photo_image", 300, [[1024, 570, "photo"]]),
                             sample("photo_read", 1400, [SHEET2, SHEET4], args=2400),
                             sample("photo_read", 120, [], args=60),                       # a drop: no sheet
                             sample("photo_compare", 1200 + 780, [cmp_img] * 3 + [ZOOM]),  # one zoom
                             sample("photo_compare", 1100, [cmp_img] * 2),                # no zoom asked
                             sample("facet_edit", 1400, [[1024, 454, "chart"], [1022, 576, "sections"]] + [cmp_img] * 5,
                                    status="applied", args=1500),
                             sample("facet_edit", 200, [], status="refused"),
                             sample("facet_variants", 700, [[1280, 899, "variants"]] * 2),
                             sample("facet_finish", 300, [], status="accepted")]}


def test_token_formula_and_steps():
    assert cli.image_tokens(1024, 454) == pytest.approx(0.975 * 32 * 15 + 112)
    step = cli.mix_step(rehearsal()["tool_outputs"], {"facet_edit": 1})
    edit = cli.call_tokens(rehearsal()["tool_outputs"][6])
    assert step["tokens"] == pytest.approx(edit["tokens"]) and step["results"] == 1.0    # refused edits are not priced
    assert edit["stubbable"] == pytest.approx(5 * cli.image_tokens(512, 400)) and edit["stub_images"] == 5
    with pytest.raises(ValueError, match="no successful"):
        cli.mix_step(rehearsal()["tool_outputs"], {"facet_restore": 1})


def test_the_mixes_are_the_v3_tools():
    """The typical mix is the method the v3 instructions teach (edits, zoomed compares, a photo_read now and then, a
    variants bracket, a closer look, an inspect), every tool of every mix a v3 tool; the range's arms are every reply
    an edit, the typical mix and a read-heavy one (the typical mix with photo_read 3 in 10 in place of two edits),
    because every v3 weight is assumed (review 2026-10-12, P2)."""
    assert all(set(mix) <= set(V3_TOOLS) for mix in cli.MIXES.values()) and "photo_read" in cli.MIXES["typical"]
    assert sum(cli.MIXES["typical"].values()) == 10 and cli.MIXES["edit_heavy"] == {"facet_edit": 10}
    typical, heavy = cli.MIXES["typical"], cli.MIXES["read_heavy"]
    assert sum(heavy.values()) == 10 and heavy["photo_read"] == 3 and heavy["facet_edit"] == typical["facet_edit"] - 2
    assert {k: v for k, v in heavy.items() if k not in ("photo_read", "facet_edit")} == \
        {k: v for k, v in typical.items() if k not in ("photo_read", "facet_edit")}
    assert cli.RANGE_MIXES == ("edit_heavy", "typical", "read_heavy") and set(cli.RANGE_MIXES) == set(cli.MIXES)
    assert not any("facet_restore" in mix for mix in cli.MIXES.values())
    assert cli.READ_POINTS_MAX == 8 and cli.ZOOM_MAX == seeing.ZOOM_MAX == 3
    assert cli.high_text("read_heavy") == "a read-heavy mix (photo_read 3 in 10) at the high output"
    assert "photo_read 3 in 10" in cli.MIX_ASSUMED and "no v3 run has been measured" in cli.MIX_ASSUMED


def test_the_largest_images_are_the_tools(tmp_path):
    """The images an image-heavy call is priced at are the largest the tool makes, not the rehearsal's (review
    2026-10-12, P2): a pick sheet of PHOTOS_MAX photos (a real readings.pick_sheet of a 4-photo reading is
    read_sheet_px: 972 x 1412) and a zoom grid of four compare.ZOOM_PANEL_PX panels (1538 x 1066: 2 x 760 + 3 gaps of
    6; 2 x (500 + a 24 px label) + 3 gaps), which no zoom box passes (compare.zoom_scale) and which every real zoom
    grid of probe 1 lies within (git-ignored data)."""
    from glasses3d.agent.temple_lab import readings as RD
    from glasses3d.g3d_host import compare as C

    class Photos:
        def photo(self, rid):
            return np.full((600, 900, 3), 128, np.uint8)
    n = RD.PHOTOS_MAX
    rec = {"name": "E", "kind": "crossing", "d": 75.5, "sd_d": 1.2, "y": 9.4, "sd_y": 0.3, "X": 67.6, "sd_X": 0.9,
           "photos": [f"0{i}" for i in range(1, n + 1)], "rids": [f"r{i}" for i in range(n)],
           "picks": [{"edges": None}] * n, "uv": [[450.0, 300.0]] * n, "uv_fit": [[451.0, 300.5]] * n,
           "res_px": [0.4] * n, "res_sd": [0.4] * n}
    with Image.open(RD.pick_sheet(Photos(), rec, tmp_path / "sheet.png")) as im:
        assert im.size == cli.read_sheet_px() == (972, 1412)
    assert cli.image_tokens(*cli.read_sheet_px()) == pytest.approx(0.975 * 31 * 45 + 112)       # ~1,472
    assert cli.zoom_grid_px() == (2 * 760 + 3 * 6, 2 * (500 + 24) + 3 * 6) == (1538, 1066)
    assert tuple(C.ZOOM_PANEL_PX) == (760, 500) and max(cli.zoom_grid_px()) <= C.SHEET_MAX_PX
    assert cli.image_tokens(*cli.zoom_grid_px()) == pytest.approx(0.975 * 49 * 34 + 112)        # ~1,736
    for w, h in ((8, 8), (240, 160), (380, 220), (380, 250), (760, 500), (1200, 300), (200, 900), (1999, 1333)):
        k = C.zoom_scale(w, h)
        assert round(w * k) <= 760 and round(h * k) <= 500, (w, h)
    grids = sorted((REVIEW / "v3/probe1/pkg/work/compare/cache").glob("*/zoom-*.png"))
    for p in (g for g in grids if "_" not in g.name):
        with Image.open(p) as im:
            assert im.width <= cli.zoom_grid_px()[0] and im.height <= cli.zoom_grid_px()[1], p


def test_v3_images_are_priced_at_the_most_one_call_returns():
    """photo_read is priced at READ_POINTS_MAX pick sheets of the largest size, its text and arguments scaled by the
    same factor; a call with no pick sheet (a drop) is left out of photo_read's mean, so it never dilutes the price of
    a read (review 2026-10-12, P2: one drop call halved it); photo_compare at its compare images plus ZOOM_MAX zoom
    grids of the largest size and the text of each zoom beyond its own: its own zoom grids (recorded as
    ZOOM_GRID_KIND, cli.record_kind) are replaced, never added to (v3p3-t5: recorded as "compare", a 2-zoom call was
    priced at 5 grids); a call recorded before record_kind (its grids "compare" images) keeps them and is priced up to
    ZOOM_MAX grids high; the recorded call never changes; a rehearsal without a pick sheet is refused while a mix prices
    photo_read, one without a zoom grid is not (the grid's size is the tool's)."""
    outs = rehearsal()["tool_outputs"]
    read, drop, cmp_zoom, cmp_plain, edit = outs[2], outs[3], outs[4], outs[5], outs[6]
    sheet, grid = [*cli.read_sheet_px(), "reading"], [*cli.zoom_grid_px(), cli.ZOOM_GRID_KIND]
    p = cli.priced_sample(read, 4)
    assert p["images"] == [sheet] * 8 and (p["text_chars"], p["args_chars"]) == (1400 * 4, 2400 * 4)
    assert cli.call_tokens(p)["tokens"] == pytest.approx(0.42 * (1400 + 2400) * 4 + 8 * cli.image_tokens(972, 1412))
    assert (read["images"], read["text_chars"], read["args_chars"]) == ([SHEET2, SHEET4], 1400, 2400)   # as recorded
    d = cli.priced_sample(drop, 4)
    assert d["unpriced"] is True and d["images"] == [] and "unpriced" not in drop
    z = cli.priced_sample(cmp_zoom, 4)
    assert z["images"] == cmp_zoom["images"][:3] + [grid] * 3                # its own zoom replaced by the 3 priced
    assert z["text_chars"] == cmp_zoom["text_chars"] + 2 * cli.ZOOM_TEXT_CHARS
    assert cmp_zoom["images"][3] == ZOOM and len(cmp_zoom["images"]) == 4     # as recorded
    old = {**cmp_zoom, "images": cmp_zoom["images"][:3] + [ZOOM_AS_COMPARE]}  # a rehearsal before record_kind
    zo = cli.priced_sample(old, 4)
    assert zo["images"] == old["images"] + [grid] * 3                       # its grid stays: priced up to 3 high
    assert zo["text_chars"] == old["text_chars"] + 3 * cli.ZOOM_TEXT_CHARS
    assert cli.priced_sample(cmp_plain, 4)["images"] == cmp_plain["images"] + [grid] * 3
    assert cli.priced_sample(edit, 4) is edit                                # facet_edit's compare has no zoom
    # the guard replaces a compare's panels and its zoom grids (lab.context_stubs); no pick sheet is counted stubbable
    assert cli.call_tokens(z)["stub_images"] == 6 and cli.call_tokens(zo)["stub_images"] == 7
    assert cli.call_tokens(p)["stub_images"] == 0
    # a drop does not dilute a read: photo_read's mean is the full call's, with or without the drop in the rehearsal
    priced = cli.priced_samples(outs, 4)
    full = cli.call_tokens(p)["tokens"]
    assert cli.mix_step(priced, {"photo_read": 1})["tokens"] == pytest.approx(full)
    assert cli.mix_step(cli.priced_samples([s for s in outs if s is not drop], 4), {"photo_read": 1}) == \
        cli.mix_step(priced, {"photo_read": 1})
    nosheet = [{**s, "images": [i for i in s["images"] if i[2] != "reading"]} for s in outs]
    for bad in (nosheet, [s for s in outs if s is not read]):                    # only a drop: nothing to price
        with pytest.raises(ValueError, match="no photo_read call with a pick sheet"):
            cli.priced_samples(bad, 4)
    nozoom = [{**s, "images": [i for i in s["images"] if i is not cmp_zoom["images"][-1]]} for s in outs]
    assert cli.priced_samples(nozoom, 4)[4]["images"].count(grid) == 3          # priced from the tool's own size
    # the typical step: each tool's mean priced call at its mix share (the drop left out of photo_read's)
    t = cli.facet_estimate(rehearsal())
    mix = cli.MIXES["typical"]
    share = {k: v / sum(mix.values()) for k, v in mix.items()}

    def tokens(s):
        return cli.call_tokens(cli.priced_sample(s, 4))["tokens"]
    want = (share["photo_read"] * tokens(read) + share["photo_compare"] * (tokens(cmp_zoom) + tokens(cmp_plain)) / 2
            + share["facet_edit"] * tokens(edit) + share["facet_variants"] * tokens(outs[8])
            + share["photo_image"] * tokens(outs[1]) + share["facet_inspect"] * tokens(outs[0]))
    assert t["steps"]["typical"]["tokens"] == pytest.approx(want, abs=0.2)
    assert (t["read_points"], t["zoom_grids"], t["mixes"], t["range_mixes"]) == (8, 3, cli.MIXES,
                                                                                 list(cli.RANGE_MIXES))
    assert (t["pick_sheet_px"], t["zoom_grid_px"], t["zoom_grid_kind"], t["zoom_text_chars"]) == \
        ([972, 1412], [1538, 1066], cli.ZOOM_GRID_KIND, cli.ZOOM_TEXT_CHARS)
    rows = t["tool_outputs"]["photo_read"]
    assert rows[0]["images"] == [SHEET2, SHEET4] and "priced" not in rows[0]                   # as recorded
    assert rows[1]["priced"] is False                                                           # the drop
    assert t["tool_outputs"]["photo_compare"][0]["images"] == cmp_zoom["images"]                # as recorded
    # three own zooms: all three replaced, no text added
    three = sample("photo_compare", 1980, [[512, 400, "compare"]] * 3 + [ZOOM] * 3)
    z3 = cli.priced_sample(three, 4)
    assert z3["images"] == [[512, 400, "compare"]] * 3 + [[1538, 1066, cli.ZOOM_GRID_KIND]] * 3
    assert z3["text_chars"] == 1980


def test_estimate_table_guard_and_long_context():
    table = cli.facet_estimate(rehearsal())
    rows = {(r["requests"], r["effort"]): r for r in table["rows"]}
    assert set(rows) == {(n, e) for n in cli.RUN_LENGTHS for e in ("high", "xhigh", "max")}
    exp = [rows[(n, "max")]["expected"]["expected_micro_usd"] for n in cli.RUN_LENGTHS]
    assert exp == sorted(exp) and all(rows[k]["high"]["expected_micro_usd"] >= rows[k]["expected"]["expected_micro_usd"]
                                      for k in rows)
    assert rows[(40, "high")]["expected"] == rows[(40, "max")]["expected"]          # high is priced as max
    assert rows[(40, "xhigh")]["expected"] == rows[(40, "max")]["expected"]  # no invented discount
    step = {k: float(v) for k, v in table["steps"]["edit_heavy"].items()}
    c0 = table["first_request"]["tokens"]
    guarded = cli.project(c0, step, 90, output=580, max_output_tokens=16_384)
    open_ = cli.project(c0, step, 90, output=580, max_output_tokens=16_384, guard_tokens=None)
    assert guarded["context_guard_request"] is not None and open_["context_guard_request"] is None
    assert open_["first_long_context_request"] is not None
    assert guarded["peak_input_tokens"] < open_["peak_input_tokens"]
    assert guarded["expected_micro_usd"] < open_["expected_micro_usd"]
    first = cli.project(c0, step, 1, output=580, max_output_tokens=16_384)
    assert first["expected_micro_usd"] > 0 and first["last_input_tokens"] == c0


def test_the_range_top_is_the_costliest_listed_mix():
    """range_top: the costliest of RANGE_MIXES (every reply an edit, the typical mix, the read-heavy mix) at the high
    output; its cap the largest of theirs; the table's text names which (review 2026-10-12, P2: with only the typical
    mix and edits, a run reading 2-3 in 10 passed the printed top)."""
    t = cli.facet_estimate(rehearsal())
    steps = {k: {kk: float(vv) for kk, vv in v.items()} for k, v in t["steps"].items()}
    c0 = t["first_request"]["tokens"]
    runs = {m: cli.project(c0, steps[m], 60, output=cli.OUTPUT_TOKENS["high"], max_output_tokens=16_384)
            for m in cli.RANGE_MIXES}
    top = cli.range_top(c0, steps, 60, max_output_tokens=16_384)
    want = max(runs, key=lambda m: runs[m]["expected_micro_usd"])
    assert top["mix"] == want and top["expected_micro_usd"] == runs[want]["expected_micro_usd"]
    assert top["required_cap_micro_usd"] == max(r["required_cap_micro_usd"] for r in runs.values())
    # this rehearsal's edits are small beside 8 full pick sheets: the read-heavy arm is the top, above the typical
    assert want == "read_heavy" and runs["read_heavy"]["expected_micro_usd"] > runs["typical"]["expected_micro_usd"]
    assert all(r["text"].endswith("(a read-heavy mix (photo_read 3 in 10) at the high output)") for r in t["rows"])
    big = rehearsal()             # edits of 9 large compare panels, before the guard (its token epoch and its byte
    #                               rung, 256 MB: these 20 requests reach about 90 MB): every reply an edit costs most
    big["tool_outputs"][6] = {**big["tool_outputs"][6], "images": [[1024, 1024, "compare"]] * 9}
    tb = cli.facet_estimate(big, requests=(20,))
    assert all(r["high"]["context_guard_request"] is None and r["high"]["peak_request_mb"] < 256 for r in tb["rows"])
    assert {r["high"]["mix"] for r in tb["rows"]} == {"edit_heavy"}
    assert all(r["text"].endswith("(every reply an edit)") for r in tb["rows"])


def test_a_run_that_reaches_the_input_limit_ends_there():
    """A projected request whose input would pass the model's input limit is never sent (the budget guard refuses it),
    so the modelled run ends before it: project names it and prices the requests before it, and the table's text and
    the approval text say so instead of failing (a 90-reply run of large images reached it: PricingError before)."""
    from glasses3d.agent.pricing import MODEL_MAX_INPUT_TOKENS
    step = {"tokens": 15_000.0, "stubbable": 0.0, "results": 0.0, "stub_images": 0.0}
    run = cli.project(30_000, step, 90, output=2_000, max_output_tokens=16_384)
    at = run["input_limit_request"]
    assert at is not None and 30_000 + (at - 2) * 15_000 <= MODEL_MAX_INPUT_TOKENS < 30_000 + (at - 1) * 15_000
    before = cli.project(30_000, step, at - 1, output=2_000, max_output_tokens=16_384)
    assert before["input_limit_request"] is None and run["expected_micro_usd"] == before["expected_micro_usd"]
    assert run["peak_input_tokens"] <= MODEL_MAX_INPUT_TOKENS
    assert cli.limit_text(run) == (f"; its context reaches the model's {MODEL_MAX_INPUT_TOKENS:,}-token input limit at "
                                   f"request {at}, where such a run ends") and cli.limit_text(before) == ""
    big = rehearsal()           # 8 large edit images: the 90-reply top reaches the limit at request 84 (6 images were a
    #                             knife edge: once the zoom compare's own grid was no longer priced twice, record_kind,
    #                             that run peaked at 921,645 tokens, 355 under the limit)
    big["tool_outputs"][6] = {**big["tool_outputs"][6], "images": [[1536, 1536, "compare"]] * 8}
    rows = {r["requests"]: r for r in cli.facet_estimate(big)["rows"] if r["effort"] == "max"}
    assert rows[90]["high"]["input_limit_request"] is not None and "input limit at request" in rows[90]["text"]
    assert rows[40]["high"]["input_limit_request"] is None and "input limit" not in rows[40]["text"]


def test_request_estimate_block(tmp_path):
    """The approval block: 60 replies when the run has no reply cap, a cap rounded to $5, the text and limitation
    saying the v3 mix is assumed and "up to" covers only the listed mixes (review 2026-10-12, P2), the image pricing
    it was made with; a table made for another mix, range or image pricing is refused."""
    table = cli.facet_estimate(rehearsal())
    s = T.run_settings(effort="max", max_turns=None)
    block = cli.request_estimate(json.loads(json.dumps(table)), s, 30_000, [[1226, 682]] * 6)
    assert block["requests"] == 60 and block["text"].startswith("Illustrative 60-reply projection")
    assert int(block["suggested_cap_micro_usd"]) % 5_000_000 == 0 and "No approval" in block["limitation"]
    assert block["text"].endswith(" at effort max. " + cli.MIX_ASSUMED) and cli.MIX_ASSUMED in block["limitation"]
    assert "with a read-heavy mix (photo_read 3 in 10) at the high output)" in block["text"]
    assert (block["source"]["read_points"], block["source"]["zoom_grids"], block["source"]["pick_sheet_px"],
            block["source"]["zoom_grid_px"]) == (8, 3, [972, 1412], [1538, 1066])
    A.canonical_json(block)                                                           # plain JSON
    # a table made for v2's tool mix, before the read-heavy arm, or at other image pricing is refused
    v2 = {**table, "mixes": {"typical": {"facet_edit": 5, "facet_variants": 1, "photo_compare": 2, "photo_image": 1,
                                         "facet_inspect": 1}, "edit_heavy": {"facet_edit": 10}}}
    two = {**table, "mixes": {k: v for k, v in table["mixes"].items() if k != "read_heavy"},
           "range_mixes": ["edit_heavy", "typical"]}
    for old in (v2, two, {k: v for k, v in table.items() if k != "read_points"},
                {k: v for k, v in table.items() if k != "zoom_grids"},
                {k: v for k, v in table.items() if k != "range_mixes"},
                {**table, "pick_sheet_px": [972, 728]}, {**table, "zoom_grid_px": [1538, 1006]},
                {**table, "zoom_grid_kind": "compare"}, {**table, "zoom_text_chars": 0}):    # compare: before record_kind
        with pytest.raises(ValueError, match="another tool mix or image pricing"):
            cli.request_estimate(old, s, 30_000, [[1226, 682]] * 6)


def test_the_estimates_image_kinds_are_the_labs(tmp_path):
    """The estimate's image kinds are a real Lab's (review 2026-10-12, P3): a real photo_compare's zoom grid is
    recorded as ZOOM_GRID_KIND and its panels as "compare" (cli.record_kind of the lab's index entries), and once
    that compare is an old result (two later results, not best_checkpoint's) context_stubs replaces each exactly when
    its recorded kind is in STUBBED_KINDS; among the kinds the lab registers, it replaces exactly LAB_STUB_KINDS
    (compare images and variant sheets; never charts or photos), and STUBBED_KINDS adds nothing but the zoom grids:
    the projection stubs what the guard stubs. A pick sheet is replaced only once its reading was read again or
    dropped (the stored reading's own sheet stays): the projection counts no pick sheet as stubbable, which prices a
    run that re-reads a little high, never low."""
    root, _ = package(tmp_path)
    lab = lab_on(root)
    before = set(lab.state["images"])
    out = lab.compare("cmp-zoom", ["05"], None, [{"photo": "05", "box": [40, 60, 200, 140], "geometry": True}])
    assert out["status"] == "ok"
    new = {d: r for d, r in lab.state["images"].items() if d not in before}
    zooms = {d: r for d, r in new.items() if r.get("zoom")}
    panels = {d: r for d, r in new.items() if not r.get("zoom")}
    assert zooms and {cli.record_kind(r) for r in zooms.values()} == {cli.ZOOM_GRID_KIND}
    assert panels and {cli.record_kind(r) for r in panels.values()} == {"compare"}
    for cid, pid in (("cmp-later-1", "01"), ("cmp-later-2", "02")):           # the zoomed compare becomes old
        assert lab.compare(cid, [pid], None, None)["status"] == "ok"
    assert lab.state.get("best_checkpoint") is None
    real = lab.context_stubs(list(zooms) + list(panels))
    assert {d for d in zooms if d in real} == ({*zooms} if cli.ZOOM_GRID_KIND in cli.STUBBED_KINDS else set())
    assert {d for d in panels if d in real} == ({*panels} if "compare" in cli.STUBBED_KINDS else set())
    assert set(cli.STUBBED_KINDS) - set(cli.LAB_STUB_KINDS) <= {cli.ZOOM_GRID_KIND}
    kinds = ("compare", "variants", "chart", "photo")
    digests = {}

    def image(name, kind, **meta):
        p = root / "work" / f"kind-{name}.png"
        p.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (8, 8), (len(digests) * 20, 7, 9)).save(p)
        lab._image(p, kind, **meta)
        digests[name] = sha(p)
    for kind in kinds:
        image(kind, kind, ref="an-old-result", photo="05", revision=0)
    lab.state["readings"] = {"E": {"id": "read009-E"}}
    image("sheet-stored", "reading", ref="read009", reading="E", revision=0)       # the stored reading's own sheet
    image("sheet-superseded", "reading", ref="read003", reading="E", revision=0)   # E read again since
    stubbed = lab.context_stubs(list(digests.values()))
    assert {k for k in kinds if digests[k] in stubbed} == set(cli.LAB_STUB_KINDS) & set(kinds) == \
        set(cli.STUBBED_KINDS) & set(kinds)
    assert digests["sheet-stored"] not in stubbed and digests["sheet-superseded"] in stubbed
    assert cli.ZOOM_GRID_KIND in cli.STUBBED_KINDS and not set(cli.READING_KINDS) & set(cli.STUBBED_KINDS)


PAID_FACET_RUNS = {"facet-astra-002": "package", "facet-astra-003": "package_003", "facet-astra-004": "package_004"}


def test_output_tokens_are_the_paid_facet_runs():
    """Output tokens per request are priced from the paid gpt-6-astra facet runs (review of the build, P2: the v1
    lab run's 580 and the Studio median 851 priced every facet run about 30 % low): expected = run 4's mean, high =
    the highest run mean (run 3), all at effort max. Where the runs' budget files are present (git-ignored data),
    their usage rows give exactly these means."""
    assert cli.OUTPUT_TOKENS == {"expected": 1852, "high": 2064}
    table = cli.facet_estimate(rehearsal())
    assert table["output_tokens_per_request"] == cli.OUTPUT_TOKENS
    assert "-004 1852 (32)" in table["output_tokens_source"] and "-003 2064 (15)" in table["output_tokens_source"]
    means = {}
    for rid, pkg in PAID_FACET_RUNS.items():
        p = PRADA.LAB_DATA / pkg / "runs" / rid / "budget.json"
        if p.is_file():
            outs = [r["usage"]["output_tokens"] for r in json.loads(p.read_text(encoding="utf-8"))["rows"]
                    if (r.get("usage") or {}).get("output_tokens") is not None]
            means[rid] = round(sum(outs) / len(outs))
    if len(means) == len(PAID_FACET_RUNS):
        assert means == {"facet-astra-002": 1635, "facet-astra-003": 2064, "facet-astra-004": 1852}
        assert cli.OUTPUT_TOKENS == {"expected": means["facet-astra-004"], "high": max(means.values())}


def test_facet_variants_is_priced_at_the_jobs_most_sheets(tmp_path):
    """One facet_variants call returns one sheet per photo, up to 4 when the job has photos of another colour with a
    camera (3 target photos + 1), else 3 (Lab.variant_photos_max). The rehearsal's call is priced at that many
    sheets of the largest page (sheets.SHEET_W x SHEET_H, review 2026-10-12 P3: not the rehearsal's own sheet), and a
    request for a package whose calls can return more sheets than the estimate table priced is refused."""
    targets = [{"role": "target"}] * 6
    assert cli.variant_sheets(targets) == 3 and cli.variant_sheets(targets + [{"role": "reference"}]) == 3
    assert cli.variant_sheets(targets + [{"role": "colourway"}]) == 4 == cli.variant_sheets(
        targets + [{"role": "colourway"}] * 3)
    two = sample("facet_variants", 700, [[1280, 692, "variants"], [1280, 899, "variants"]])
    four = cli.priced_sample(two, 4)
    page = [SH.SHEET_W, SH.SHEET_H, "variants"]
    assert four["images"] == [page] * 4 and page[:2] == [1280, 900]
    assert cli.priced_sample(two, 2)["images"] == [page] * 2
    assert two["images"] == [[1280, 692, "variants"], [1280, 899, "variants"]]   # the recorded call is not changed
    edit = rehearsal()["tool_outputs"][6]
    assert cli.priced_sample(edit, 4) is edit                                  # facet_edit is never padded
    t3, t4 = cli.facet_estimate(rehearsal(), sheets=3), cli.facet_estimate(rehearsal())
    assert (t3["variant_sheets"], t4["variant_sheets"]) == (3, 4)
    share = cli.MIXES["typical"]["facet_variants"] / sum(cli.MIXES["typical"].values())
    grow = t4["steps"]["typical"]["tokens"] - t3["steps"]["typical"]["tokens"]
    assert grow == pytest.approx(share * cli.image_tokens(1280, 900), abs=0.2)  # one more sheet, at its mix share
    assert t4["steps"]["edit_heavy"] == t3["steps"]["edit_heavy"]               # that mix has no variants call
    assert t4["tool_outputs"]["facet_variants"][0]["images"] == [[1280, 899, "variants"]] * 2   # as recorded
    # the rehearsal's job: its own package's photo rows (4 with another colour, 3 without, the most when unreadable)
    lab_json = tmp_path / "pkg" / "inputs" / "lab.json"
    lab_json.parent.mkdir(parents=True)
    for rows, want in ((targets, 3), (targets + [{"role": "colourway"}], 4)):
        lab_json.write_text(json.dumps({"photos": rows}), encoding="utf-8")
        assert cli.rehearsal_variant_sheets({"prepared": {"path": str(tmp_path / "pkg")}}) == want
    assert cli.rehearsal_variant_sheets({}) == cli.rehearsal_variant_sheets(
        {"prepared": {"path": str(tmp_path / "none")}}) == 4
    s = T.run_settings(effort="max", max_turns=40)
    sizes = [[1226, 682]] * 6
    with pytest.raises(ValueError, match="prices facet_variants at 3 sheets a call, this package's calls return up "
                                         "to 4: run estimate"):
        cli.request_estimate(t3, s, 30_000, sizes, variant_sheets=4)
    old = {k: v for k, v in t4.items() if k != "variant_sheets"}                # a table made before this rule
    with pytest.raises(ValueError, match="at the recorded sheets"):
        cli.request_estimate(old, s, 30_000, sizes, variant_sheets=3)
    block = cli.request_estimate(t4, s, 30_000, sizes, variant_sheets=4)
    assert cli.request_estimate(t3, s, 30_000, sizes, variant_sheets=3)["expected_micro_usd"] < \
        block["expected_micro_usd"]
    assert block["source"]["variant_sheets"] == 4 and block["source"]["output_tokens"] == cli.OUTPUT_TOKENS
    # make_spec binds the package's own limit (B1-B3 have cameras: 4 sheets)
    root, _ = package(tmp_path / "p")
    lab = lab_on(root)
    assert lab.variant_photos_max() == 4
    for name, table in (("t3.json", t3), ("t4.json", t4)):
        (tmp_path / name).write_text(json.dumps(table), encoding="utf-8")
    with pytest.raises(ValueError, match="run estimate"):
        cli.make_spec(root, "r3", s, "20", write_request=True, lab=lab, estimate_path=tmp_path / "t3.json")
    cli.make_spec(root, "r4", s, "20", write_request=True, lab=lab, estimate_path=tmp_path / "t4.json")
    doc = json.loads((root / "runs" / "r4" / "approval-request.json").read_text(encoding="utf-8"))
    assert doc["estimate"]["source"]["variant_sheets"] == 4


def test_facet_variants_text_is_priced_at_the_most_arms_and_inspect_at_its_largest():
    """Review 2026-10-12, P3 (checked on the T5 v3 rehearsal and session2): facet_variants' text and arguments are
    priced at VARIANTS_MAX arms (by VARIANTS_MAX / its own arms when the rehearsal records them, as recorded or from
    the report's outputs; else VARIANTS_MAX / VARIANTS_MIN: high), and facet_inspect at its largest successful call
    (its text grows with the stored readings: revision 0's inspect priced the T5 v3 mean at 1,682 tokens against
    session2's 2,908-3,167); every other inspect is left out of the mean, never the recorded calls changed."""
    from glasses3d.agent.temple_lab.lab import VARIANTS_MAX, VARIANTS_MIN
    v = sample("facet_variants", 1500, [[1060, 898, "variants"]] * 2, args=1400)
    assert (VARIANTS_MIN, VARIANTS_MAX) == (2, 4)          # v3-final: 6 -> 4 (one sheet per photo, sheets.py)
    three_arms = cli.priced_sample({**v, "arms": 3}, 4)
    assert (three_arms["text_chars"], three_arms["args_chars"]) == (2000, 1867)                      # x 4 / 3
    unknown = cli.priced_sample(v, 4)
    assert (unknown["text_chars"], unknown["args_chars"]) == (3000, 2800)                            # x 4 / 2
    assert cli.priced_sample({**v, "arms": 4}, 4)["text_chars"] == 1500 == v["text_chars"]
    assert cli.priced_sample({**v, "arms": 6}, 4)["text_chars"] == 1500     # an older record's 6 arms: never below
    assert cli.priced_sample({**v, "arms": 3}, 4)["images"] == [[1280, 900, "variants"]] * 4        # the largest page
    none = sample("facet_variants", 300, [])                                    # every arm refused: no sheet
    assert cli.priced_sample(none, 4) is none
    reh = rehearsal()
    reh["tool_outputs"].append(sample("facet_variants", 900, [[1280, 899, "variants"]]))
    reh["steps"] = [{"step": f"s{k}"} for k in range(len(reh["tool_outputs"]))]
    reh["outputs"] = {f"s{len(reh['tool_outputs']) - 1}": {"variants": [{"label": "a"}, {"label": "b"},
                                                                        {"label": "c"}]}}
    got = cli.rehearsal_samples(reh)
    assert got[-1]["arms"] == 3 and "arms" not in got[8] and "arms" not in reh["tool_outputs"][-1]
    # facet_inspect: the largest successful call prices it; a smaller one is listed unpriced
    small, big = sample("facet_inspect", 1657, [[1024, 430, "chart"]]), sample("facet_inspect", 3732,
                                                                                [[1024, 430, "chart"]])
    timed_out = sample("facet_inspect", 302, [], status="timeout")
    outs = [s for s in rehearsal()["tool_outputs"] if s["tool"] != "facet_inspect"] + [small, big, timed_out]
    priced = cli.priced_samples(outs, 4)
    flags = [p.get("unpriced", False) for p in priced if p["tool"] == "facet_inspect"]
    assert flags == [True, False, False]
    assert cli.mix_step(priced, {"facet_inspect": 1})["tokens"] == pytest.approx(cli.call_tokens(big)["tokens"])
    assert cli.LARGEST_PRICED == ("facet_inspect",) and "unpriced" not in small
    table = cli.facet_estimate({**rehearsal(), "tool_outputs": outs})
    assert [r.get("priced", True) for r in table["tool_outputs"]["facet_inspect"]] == [False, True, True]
    assert table["variant_sheet_px"] == [1280, 900] and table["variant_arms"] == 4
    assert table["largest_priced"] == ["facet_inspect"]


def test_request_bytes_model_and_the_guards_byte_rungs():
    """Review 2026-10-12, P2 (v3p3-verify-15): every projected run carries its request bytes and images (each image at
    its kind's largest measured bytes per token, text at 1.1 bytes a character, REQUEST_OVERHEAD_BYTES a request),
    its peak, the count's modelled time there beside its timeout (budget.count_timeout_s), the guard's epochs (the
    token epoch once, then byte rungs at k x guard_bytes: loop.ContextGuard) and the request where the provider's
    per-request limit would end it (budget.PAYLOAD_LIMIT_BYTES / _IMAGES)."""
    assert cli.BYTES_PER_IMAGE_TOKEN == {"chart": 353, "compare": 412, "photo": 214, "reading": 230,
                                         "variants": 414, "zoom": 324} and cli.BYTES_PER_IMAGE_TOKEN_MAX == 414
    assert cli.image_bytes(1024, 454, "chart") == pytest.approx(cli.image_tokens(1024, 454) * 353)
    assert cli.image_bytes(1024, 454, "sections") == pytest.approx(cli.image_tokens(1024, 454) * 414)
    edit = sample("facet_edit", 1400, [[1024, 454, "chart"]] + [[512, 400, "compare"]] * 5, status="applied",
                  args=1500)
    t = cli.call_tokens(edit)
    assert t["bytes"] == pytest.approx(1.1 * 2900 + cli.image_bytes(1024, 454, "chart")
                                       + 5 * cli.image_bytes(512, 400, "compare"))
    assert t["stub_bytes"] == pytest.approx(5 * cli.image_bytes(512, 400, "compare")) and t["images"] == 6
    assert cli.first_request_bytes(1000, [[100, 100]]) == round(1100 + 24_000 + cli.image_bytes(100, 100))
    assert cli.first_request_bytes(1000, [[100, 100]], [5000]) == 1100 + 24_000 + 5000
    # no bytes known: the bytes fields are None and the byte rungs off (the token projection unchanged)
    step = {"tokens": 10_000.0, "stubbable": 6_000.0, "results": 1.0, "stub_images": 5.0, "bytes": 3_000_000.0,
            "stub_bytes": 2_500_000.0, "images": 6.0}
    plain = cli.project(30_000, {k: step[k] for k in ("tokens", "stubbable", "results", "stub_images")}, 60,
                        output=2_000, max_output_tokens=16_384)
    assert plain["peak_request_mb"] is plain["peak_images"] is plain["peak_count_s"] is None
    assert plain["context_guard_requests"] == [plain["context_guard_request"]]                       # one token epoch
    sized = cli.project(30_000, step, 60, output=2_000, max_output_tokens=16_384, c0_bytes=2_650_000, c0_images=9,
                        guard_bytes=None)
    assert sized["expected_micro_usd"] == plain["expected_micro_usd"] and sized["context_guard_requests"] == \
        plain["context_guard_requests"]
    g = sized["context_guard_request"]                                     # images regrow after the token epoch
    drop = (g - 2) - cli.GUARD_KEEP_RESULTS                                # results before it, but the kept ones
    assert g == 23 and sized["peak_images"] == math.ceil(9 + 59 * 6 - drop * 5)      # the last request is the peak
    assert sized["peak_count_s"] == round(cli.COUNT_S_BASE + cli.COUNT_S_PER_MB * sized["peak_request_mb"], 1)
    assert abs(sized["count_timeout_s_at_peak"] - B.count_timeout_s(sized["peak_request_mb"] * 1e6)) <= 1
    # byte rungs: with a 40 MB rung the guard also fires on bytes, each rung once, never on tokens again
    rungs = cli.project(30_000, step, 60, output=2_000, max_output_tokens=16_384, c0_bytes=2_650_000, c0_images=9,
                        guard_bytes=40_000_000)
    assert len(rungs["context_guard_requests"]) >= 2 and rungs["context_guard_request"] < g
    assert rungs["peak_request_mb"] < sized["peak_request_mb"]
    # the provider's per-request limit ends the run before the request that would pass it
    big = {**step, "images": 100.0}
    capped = cli.project(30_000, big, 60, output=2_000, max_output_tokens=16_384, c0_bytes=2_650_000, c0_images=9,
                         guard_tokens=None)
    at = capped["payload_limit_request"]
    assert at is not None and 9 + (at - 2) * 100 <= B.PAYLOAD_LIMIT_IMAGES < 9 + (at - 1) * 100
    before = cli.project(30_000, big, at - 1, output=2_000, max_output_tokens=16_384, c0_bytes=2_650_000,
                         c0_images=9, guard_tokens=None)
    assert before["payload_limit_request"] is None and before["expected_micro_usd"] == capped["expected_micro_usd"]
    assert cli.limit_text(capped) == (f"; its requests reach the provider's per-request limit (512 MB or 1,500 images) "
                                      f"at request {at}, where such a run ends") and cli.limit_text(before) == ""


def test_the_table_and_the_approval_carry_request_bytes(tmp_path):
    """The table projects every row's bytes (the first request's recorded bytes when the rehearsal has them, else the
    bytes model) and the approval block uses the package's own photo bytes and the settings' guard."""
    reh = rehearsal()
    table = cli.facet_estimate(reh)
    assert table["first_request"]["bytes_from"] == "bytes model" and table["context_guard_bytes"] == \
        L.CONTEXT_GUARD_BYTES
    reh["first_request"]["bytes"] = 2_650_000
    recorded = cli.facet_estimate(reh)
    assert recorded["first_request"]["request_bytes"] == 2_650_000 and recorded["first_request"]["bytes_from"] == \
        "recorded" and table["first_request"]["request_bytes"] == cli.first_request_bytes(30_000, [[1226, 682]] * 6)
    for r in recorded["rows"]:
        for run in (r["expected"], r["high"]):
            assert run["peak_request_mb"] > 2.65 and run["peak_images"] >= 6 and run["peak_count_s"] > 0
    for k in ("variant_sheet_px", "variant_arms", "largest_priced", "bytes_per_image_token", "text_bytes_per_char",
              "request_overhead_bytes", "context_guard"):
        assert recorded[k] == cli.image_pricing()[k]
        with pytest.raises(ValueError, match="another tool mix or image pricing"):
            cli.request_estimate({kk: v for kk, v in recorded.items() if kk != k}, T.run_settings(effort="max",
                                                                                                  max_turns=40),
                                 30_000, [[1226, 682]] * 6)
    s = T.run_settings(effort="max", max_turns=40)
    block = cli.request_estimate(recorded, s, 30_000, [[1226, 682]] * 6, image_bytes=[300_000] * 6)
    assert block["first_request_bytes"] == 33_000 + 24_000 + 1_800_000
    assert block["expected"]["peak_request_mb"] is not None and block["high"]["peak_images"] is not None
    # make_spec gives the package's own photo bytes (each data URL's length)
    root, _ = package(tmp_path)
    lab = lab_on(root)
    (tmp_path / "t.json").write_text(json.dumps(recorded), encoding="utf-8")
    spec, _ = cli.make_spec(root, "r1", s, "20", write_request=True, lab=lab, estimate_path=tmp_path / "t.json")
    doc = json.loads((root / "runs" / "r1" / "approval-request.json").read_text(encoding="utf-8"))
    chars = cli.c0_chars(spec.instructions, spec.brief, spec.tools, spec.photos)
    assert doc["estimate"]["first_request_bytes"] == cli.first_request_bytes(
        chars, [p.size_px for p in spec.photos], [len(p.image_url) for p in spec.photos])


@pytest.mark.parametrize("effort", ["max", "xhigh"])
def test_request_defaults_to_the_v3_table_and_refuses_without_a_traceback(tmp_path, capsys, monkeypatch, effort):
    """Review 2026-10-12, P3 (T5 open item 2): `cli request` defaulted to the v2 table (estimate.json), which
    request_estimate refuses, and main let the ValueError out as a traceback. The default is now the v3 table, and a
    package or table refusal prints {"status": "refused"} and exits 2 (nothing is written or sent)."""
    assert cli.ESTIMATE == cli.LAB_DATA / "estimate_v3.json"
    root, _ = package(tmp_path)
    monkeypatch.setattr(cli, "Lab", lambda _root: lab_on(root))          # a real Lab on fakes (no Blender)
    v2 = tmp_path / "v2.json"
    v2.write_text(json.dumps({"variant_sheets": 4, "mixes": {"typical": {"facet_edit": 5}}, "steps": {}}),
                  encoding="utf-8")
    with pytest.raises(SystemExit) as stop:
        cli.main(["request", "--job", str(root), "--effort", effort, "--turns", "40", "--cap", "20", "--estimate",
                  str(v2), "--run-id", "r-v2"])
    out = json.loads(capsys.readouterr().out)
    assert stop.value.code == 2 and out["status"] == "refused" and out["type"] == "ValueError"
    assert "another tool mix or image pricing" in out["message"]
    assert not (root / "runs" / "r-v2" / "approval-request.json").exists()
    if cli.ESTIMATE.is_file():          # git-ignored data: the current v3 table is one request_estimate accepts
        s = T.run_settings(effort="max", max_turns=40)
        block = cli.request_estimate(json.loads(cli.ESTIMATE.read_text(encoding="utf-8")), s, 30_000,
                                     [[1226, 682]] * 6, variant_sheets=4)
        assert block["expected"]["peak_request_mb"] is not None


def test_run_checks_the_machine_load_unless_told_to_ignore_it(tmp_path, monkeypatch):
    """Review 2026-10-12, P2 (v3p3-verify-16): `cli run` gives the run loop.machine_load, so a run refuses at preflight
    while other Blender or pytest work runs (before the scene's worker starts) and reports load that starts later;
    --ignore-load runs anyway, still reporting."""
    seen = []

    def fake_spec(job, run_id, settings, cap, *, write_request=False, estimate_path=None):
        return L.RunSpec(job_dir=job, run_id=run_id, instructions="i", tools=[], brief="b",
                         current_request_sha256="0" * 64, settings=settings), object()

    async def fake_run(spec, *, scene=None):
        seen.append((spec.load_check, spec.refuse_on_load))
        return {"status": "completed"}
    monkeypatch.setattr(cli, "make_spec", fake_spec)
    monkeypatch.setattr(cli, "run_astra", fake_run)
    base = ["run", "--job", str(tmp_path), "--effort", "max", "--turns", "40", "--cap", "20"]
    cli.main(base)
    cli.main(base + ["--ignore-load"])
    assert seen == [(L.machine_load, True), (L.machine_load, False)]


def test_main_requires_the_owner_settings(tmp_path):
    with pytest.raises(SystemExit):
        cli.main(["request", "--job", str(tmp_path)])
    with pytest.raises(SystemExit):
        cli.main(["estimate"])
    np.testing.assert_equal(cli.RUN_LENGTHS, (40, 60, 90))
