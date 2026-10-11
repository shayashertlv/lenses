"""The facet lab's package entry points (agent/temple_lab/cli.py; plan/WP6_FACETS.md §4.3-4.4): prepare with fakes
(no Blender), the clean-package and binding refusals of make_spec, the approval request's settings binding, the
instructions' contract and the facet-run estimate. The real-Blender path is T5 (tests/proofs/astra_temple_lab.py).
"""
import hashlib
import json
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
from glasses3d.agent.temple_lab import cli  # noqa: E402
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
    the real package has them: three colourway-variant basis images with supplied mattes and transported cameras, B1
    side (beside the reference photos), B2 angled and B3 front (from another folder); no reference-only photo."""
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
                        "colourway": "target", "camera": {"yaw": yaw, "pitch": pitch}}
    (tmp / "mattes").mkdir()
    black = tmp / "black"
    black.mkdir()
    colourway = []
    for pid, name, src, kind, yaw, pitch, size in (
            ("B1", "black-side.png", ref / "black-side.png", "side", 90.0, 13.6, (30, 30)),
            ("B2", "black-angled.png", black / "B2.png", "angled", 30.0, 11.4, (48, 32)),
            ("B3", "black-front.png", black / "B3.png", "front", 0.0, 10.3, (48, 32))):
        Image.new("RGB", size, (5, 5, 5 + len(kind))).save(src)
        matte = tmp / "mattes" / name
        Image.new("L", size, 255).save(matte)
        images[name] = {"image": str(src), "image_sha256": sha(src), "view_kind": kind, "colourway": "variant",
                        "camera": {"yaw": yaw, "pitch": pitch}, "matte_mode": "supplied",
                        "matte_file": f"mattes/{name}", "matte_file_sha256": sha(matte)}
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
                      reference_photos=src["reference_photos"])
    return root, out


def lab_on(root: Path) -> Lab:
    """A real Lab on the prepared package with a fake scene and compare (revision 0 = prada_default, applied and
    compared by the prepare step), as test_temple_lab.make_lab does."""
    s = FakeSession(root)
    lab = Lab(root, session=s, seeing=FakeSeeing(s, root))
    lab._basis = fake_basis()
    lab.state["start_checkpoint"], lab.state["start_fingerprint"] = "c0000", "fp-original"
    lab.state["original"] = {"footprints": {"decals": [], "hinge": []}, "chart_mesh": None}
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
    assert "- 05: side; camera yaw +90.6, pitch +13.6; the charted temple's outer face square-on (14 deg;" in brief
    assert ("- 01: side; camera yaw -90.8, pitch +13.7; the mirrored temple's outer face, from its own side, "
            "square-on (14 deg; points_px moves 0.24 mm per mm of depth error)") in brief
    assert ("- B2: angled, glossy black; camera yaw +30.0, pitch +11.4; the charted temple's outer face at a "
            "moderate angle (61 deg; points_px moves 1.78 mm per mm of depth error)") in brief
    assert ("- 03: top; camera yaw +0.3, pitch +59.9; the outer faces edge-on (90 deg); faces that point straight up "
            "at 30 deg from square-on; its compares print outer-edge offsets and the outline line") in brief
    assert "two temples): 01, 05, B1 | 02, B2 | 03 | 04, B3 | 06." in brief       # B1/B2/B3 share 05/02/04's view
    assert ("square-on (under 30 deg): 01, 05, B1. At a moderate angle (30 to 75 deg): 02, B2. Edge-on (75 deg or "
            "more): 03, 04, 06, B3.") in brief
    assert "Outer-edge offsets: 03, 06. Outline line: 03." in brief and "Glossy photos: B1, B2, B3." in brief
    assert "No finish is given for 01, 02, 03, 04, 05, 06." in brief       # the targets are not called matte
    assert f"The renders use the job's fixed light, photo_env: {cli.RIG_TEXT['photo_env']}." in brief  # the rig
    assert "The real temples' edge rounding (roll_mm) is not known" in brief
    for gone in ("reference only", "acetate", "far more clearly"):                 # no product claim in code strings
        assert gone not in brief, gone
    assert (inputs / "instructions.txt").read_bytes() == (cli.HERE / "instructions.txt").read_bytes()


def test_a_reference_only_photo_is_packaged_for_photo_image(tmp_path):
    """A photo without a camera (none for Prada now) still packages: role reference, no basis_ref, a byte copy under
    inputs/images, not in the basis; the brief and the first message say it is reference only."""
    src = sources(tmp_path)
    extra = reference_only(tmp_path)
    root = tmp_path / "job"
    cli.prepare(root, source_scene=src["scene"], scene_sha256=src["scene_sha256"], reference=src["reference"],
                basis_json=src["basis_json"], lab_class=PrepLab, import_basis=fake_import,
                colourway_photos=src["colourway_photos"], reference_photos=(extra,))
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
    assert [p["id"] for p in cli.COLOURWAY_PHOTOS] == ["B1", "B2", "B3"] and cli.REFERENCE_PHOTOS == ()
    assert [(p["image"], p["sha256"][:8]) for p in cli.COLOURWAY_PHOTOS] == [
        ("black-side.png", "6eddabbb"), ("black-angled.webp", "5705c2ca"), ("black-front.webp", "e89d19f4")]
    assert len({p["image"] for p in cli.COLOURWAY_PHOTOS}) == 3
    assert {(p["colour"], p["finish"]) for p in cli.COLOURWAY_PHOTOS} == {("black", "glossy")}
    if cli.BASIS_JSON.is_file():
        images = json.loads(cli.BASIS_JSON.read_text(encoding="utf-8"))["images"]
        for p, src in zip(cli.COLOURWAY_PHOTOS, ("05", "02", "04")):
            rec = images[p["image"]]
            assert rec["image_sha256"] == p["sha256"] and rec["colourway"] == "variant", p
            assert rec["fit_status"].startswith(f"camera transported from {src}") and rec["matte_mode"] == "supplied"
            if Path(rec["image"]).is_file():
                assert sha(rec["image"]) == p["sha256"]


def test_prepare_refusals(tmp_path):
    src = sources(tmp_path)
    kw = dict(source_scene=src["scene"], reference=src["reference"], basis_json=src["basis_json"],
              lab_class=PrepLab, import_basis=fake_import, colourway_photos=src["colourway_photos"],
              reference_photos=src["reference_photos"])
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
    assert sent == spec.instructions and "at least 5 distinct applied heads" in sent and cli.K_TOKEN not in sent
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
INSTRUCTION_NUMBERS = {"0", "1", "1.0", "2", "3", "4", "6", "10", "0.2", "0.3", "0003"}


def test_instructions_contract_and_finish_text(tmp_path):
    """instructions.txt is general for any faceted temple and photo set (owner, 2026-10-11: "yes, build 1-3"; the
    job's facts are in the brief): the tool contract, the finish preconditions verbatim, the lay-out-then-confirm
    habit and the tool semantics in general phrases, no product or photo-set facts, no retired rule."""
    raw = (cli.HERE / "instructions.txt").read_text(encoding="utf-8")
    assert raw.count(cli.K_TOKEN) == 1
    (tmp_path / "inputs").mkdir()
    (tmp_path / "inputs" / "instructions.txt").write_text(raw, encoding="utf-8")
    text = cli.instructions_text(tmp_path, 4)
    root, _ = package(tmp_path / "p")
    tools = T.registry(lab_on(root), T.run_settings(effort="max", finish_k=4))
    brief = (root / "inputs" / "brief.txt").read_text(encoding="utf-8")
    assert L.contract_problems(L.ToolContract(frozenset(T.TOOL_NAMES)), instructions=text, brief=brief,
                               tools=tools) == []
    finish = next(t for t in tools if t.name == "facet_finish").description
    pre = " ".join(finish.split("Accepted only when: ", 1)[1].split("Otherwise")[0].split())
    flat = " ".join(text.split())
    low = flat.lower()
    assert pre in flat                                                   # the preconditions verbatim, K = 4
    for banned in ("wp6", "inspect every image", "again before concluding", "not been established", "failed",
                   "owner", "earlier run", "v1",
                   # product and photo-set facts belong in the brief (cli.brief_text), never in the instructions
                   "acetate", "black photo", "almost flat", "far more clearly", "prada", "logo", "cream",
                   "the top photo", "top and rear photos", "calibrated side views"):
        assert banned not in low, banned
    assert not re.search(r"\b(?:B\d|0\d)\b", text)                    # no photo ids of a job
    for needed in (
            # tool semantics (a refusal code names its cause; the compares' text as the tools print it)
            "points_px", "E_CREASE_SHAPE", "E_JUDGEMENT", "E_BUSY", "E_TIMEOUT",
            "t_min is 1.0", "station table", "at least three non-collinear (else E_FACET_UNDERDETERMINED)",
            "spread wide (else E_ILL_CONDITIONED)", "E_RELIEF_STEP): change them together", "E_FADE_CLIFF",
            "photo_compare prints each with the start model's, facet_edit's compares omit those",
            "one image line through the model's outermost point near that d", "+-1 mm", "largest",
            "at every target photo", "1..4 photos with a camera, each in an earlier compare, at most 3 of them "
            "target photos", "that model's crease lines from 6 deg", "gloss-black", "render (target photos only)",
            "the charted temple, and the mirrored temple, its exact mirror (compares use these names)",
            # the evidence rules: two directions, a confirming view at a moderate angle, edge-on silence is no proof
            "qualitative", "never set depth or ridge/valley from which face is brighter", "two different directions",
            "at a moderate angle", "its lack of a line is no evidence", "the best evidence; say so in the note",
            "a reflection moves", "On gloss a tone step can be a crease or the edge of a reflected light",
            "measure (d, y) in square-on views; elsewhere, only check",
            "the renders use the job's fixed light (the brief names it)",
            # the habit (owner decision 1): lay out square-on, then confirm or REMOVE at a moderate angle (review of
            # the build, P1: the first layout is provisional, so the confirming view can take a crease out)
            "Lay out, then confirm", "That layout is provisional", "before refining depth",
            "Keep a crease that view also shows, remove one it contradicts (no line although it clearly shows "
            "neighbouring creases of similar angle), add one only where both views show it",
            "where it cannot resolve the region, keep the square-on reading",
            "a moderate-angle view and a glossy photo",
            # the corner rule can be decided and keeps the face whole (final review, P2)
            "two of those faces are one", "Keep that face whole",
            "bracket the candidates with facet_variants (each with node values that build it)",
            "Every facet_edit that changes which facets exist", "When those views disagree on where a crease runs",
            # only a change prompted by a number needs both views (the pre-build skeptic's correction #9)
            "A number (an offset or outline station) changes the layout only when both views show the new crease or "
            "its absence", "never split a face that no photo shows divided",
            "never merge two faces that a visible crease separates", "its own facet", "several creases meet",
            # ridge or valley: of a crease the photos show; the strip is the model; the straight-through-corner rule
            "Ridge or valley of a crease the photos show", "the section strip shows what the model built",
            "Where one face's edge runs straight through a corner, the creases there cannot all be ridges (nor all "
            "valleys): one has the other shape, or two of those faces are one", "change the node t values",
            "when it says the creases at a corner cannot all have one shape, correct the declaration the photos "
            "contradict there", "Never redeclare just to match the build",
            "never split a face to get a refused declaration built",
            # kept after the owner's run-2 remarks (barely noticeable, soft lines)
            "barely distinct, the dihedral is probably too small", "several mm at the key nodes",
            "keeps a standing neighbour's height along their shared crease",
            "fade both facets that share it the same way", "need no fade",
            "Do not fade a facet whose edges the photos show crisp", "points mostly up",
            # top bands (review P3): the side views show only where a band meets the outer face
            "Top and bottom faces", "show only where it meets the outer face",
            "Place and confirm the band's own creases, and judge its tilt, in views that see it at a moderate angle",
            # the outline line and every offset are information only (2ca5be9): one view's silhouette
            "outline line", "Like every offset it is information, one view's silhouette",
            "never change the layout for it alone",
            # the job's facts come from the brief
            "The brief says which photos show the charted temple", "the brief gives how far per mm",
            "the brief names them", "the brief says which", "the brief describes it", "the brief lists them"):
        assert needed in flat, needed
    for retired in ("on its own", "no neighbour needs to share",          # a lone fade does not dissolve its creases
                    "fewest faces", "redeclare it",                       # review of 2026-10-11 (skeptic B3)
                    "one plane across it cannot make the corner", "a corner in the top photo's outline",
                    "never from oblique views", "include b1", "a flat face has one tone", "those stations are shape",
                    "include the top photo", "1..3", "well over 10 deg", "keep roll_mm small",
                    # review of the build (2026-10-11): a station is not shape on its own (P2); a crease declaration
                    # the photos contradict is corrected, not kept to the letter (the face-4 split); the light and the
                    # top-face views come from the brief; any crease the first layout put in can be removed (P1)
                    "a station that departs from it is shape", "whether or not the start model shows it too",
                    "do not redeclare the crease", "one fixed studio light", "top faces: from above",
                    "and the section strip. declare", "change the layout only when", "is not yet a crease"):
        assert retired not in low, retired
    for number in re.findall(r"\d+(?:\.\d+)?", text):                    # no product coordinates: only rules' numbers
        assert number in INSTRUCTION_NUMBERS, number
    # about the length of the product-specific text it replaced (11,592 chars); the build review's fixes added the
    # corner rule and the provisional layout (12,236 -> 12,462 chars); the final review gave the corner rule a way to
    # decide it (variants at the moderate-angle and glossy views) and the coplanar case (-> 12,802 chars)
    assert len(raw) <= 13_000


# --------------------------------------------------------------------------------------------- brief
def test_brief_geometry_helpers():
    """The brief's photo facts from cameras: the angle to the outer face (0 square-on, 90 edge-on), temple-relative
    directions (a mirrored side view is the same direction), the bands and the camera fit texts."""
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
    assert cli._deg(-0.02) == "+0.0" and cli._deg(-90.81) == "-90.8"
    ids = {"reference-02.webp": "02"}
    moved = {"transport": {"source": "C:/x/reference-02.webp", "mask_iou_after_warp": 0.99243,
                           "mean_bidirectional_px": 0.8233}, "theta_observed": {"+1": False, "-1": False}}
    assert cli.camera_fit_text(moved, ids) == ("camera moved from 02's by matching the two photos' silhouettes (photo "
                                               "to photo, on its registration matte: IoU 0.992, mean contour 0.8 px), "
                                               "the temples' hinge opening as in 02; not checked against the model")
    checked = {**moved, "model_check": {"iou": 0.93418, "temple_iou": 0.87538,
                                        "against": "original model, white-background matte"}}
    assert cli.camera_fit_text(checked, ids) == ("camera moved from 02's by matching the two photos' silhouettes "
                                                 "(photo to photo, on its registration matte: IoU 0.992, mean contour "
                                                 "0.8 px), the temples' hinge opening as in 02; starting model's "
                                                 "silhouette on the compare's raster: IoU 0.934, temple IoU 0.875")
    assert cli.camera_fit_text({"iou": 0.87317, "contour_mean_px": 8.07}, ids) == \
        "camera fitted to the model's silhouette (all-views fit): IoU 0.873, mean contour 8.1 px"
    assert cli.camera_fit_text({}, ids) is None
    assert cli.OFFSET_VIEWS is seeing.OFFSET_VIEWS                # one list: the views whose compares print offsets
    assert cli.colour_text({"colour": "black", "finish": "glossy"}) == "glossy black"
    assert cli.colour_text({}) == "another colour"


def test_brief_of_a_job_without_another_colour_or_a_moderate_view():
    """A photo set with only target photos and no view at a moderate angle to the outer face: the brief says so
    ("none"), names no other colour and no finish (the instructions' fallback: a line in two photos under
    different light)."""
    photos = [{"id": "01", "path": "images/reference-01.png", "view": "side", "role": "target"},
              {"id": "02", "path": "images/reference-02.png", "view": "front", "role": "target"},
              {"id": "03", "path": "images/reference-03.png", "view": "rear", "role": "target"}]
    cams = {"reference-01.png": {"camera": {"yaw": 88.0, "pitch": 5.0}},
            "reference-02.png": {"camera": {"yaw": 0.0, "pitch": 5.0}},
            "reference-03.png": {"camera": {"yaw": 180.0, "pitch": 5.0}}}
    brief = cli.brief_text("Frame X", photos, cams)
    assert "At a moderate angle (30 to 75 deg): none." in brief and "two temples): 01 | 02 | 03." in brief
    assert "Outer-edge offsets: 03. Outline line: none." in brief
    assert "same model in" not in brief and "photos:" not in brief.split("Outline line")[1]
    assert "No finish is given" not in brief                                # no photo row gives a finish
    assert "- 02: front; camera yaw +0.0, pitch +5.0; the outer faces edge-on (90 deg)" in brief
    assert cli.brief_text("Frame X", photos).count("camera yaw") == 0      # no pose basis: views only


def test_brief_names_the_light_the_temples_and_the_edge_rounding():
    """The facts the instructions point to that are not per photo: the job's fixed render light (every rig the lab
    takes has a description), the names the compares give the two temples, and the real temples' edge rounding,
    "not known" unless the job knows it; finishes are listed only for the photo rows that give one."""
    photos = [{"id": "01", "path": "images/reference-01.png", "view": "side", "role": "target"},
              {"id": "B1", "path": "images/black-side.png", "view": "side", "role": "colourway", "colour": "black",
               "finish": "glossy"}]
    assert set(cli.RIG_TEXT) == set(seeing.MATCHED_RIGS)
    for rig in seeing.MATCHED_RIGS:
        brief = cli.brief_text("Frame X", photos, rig=rig)
        assert f"The renders use the job's fixed light, {rig}: {cli.RIG_TEXT[rig]}. The photos' light is " \
               "unknown." in brief
    brief = cli.brief_text("Frame X", photos)
    assert "fixed light, studio_dome_v1:" in brief                        # the default rig, the owner's choice
    assert "The charted temple is the one the spec's d, y and t chart; the mirrored temple is its mirror image" in brief
    assert "compares print outer-edge offsets per temple under these names" in brief
    assert "Glossy photos: B1.\nNo finish is given for 01.\n" in brief
    assert "The real temples' edge rounding (roll_mm) is not known: judge it from the photos' edges." in brief
    known = cli.brief_text("Frame X", photos, roll_mm=0.3)
    assert "The real temples' edge rounding is about 0.3 mm (roll_mm)." in known and "not known" not in known


def real_brief() -> str:
    """The brief the package would get from the real pose basis (data/wp6v2/basis/cameras.json, git-ignored)."""
    doc = json.loads(cli.BASIS_JSON.read_text(encoding="utf-8"))
    photos = []
    for name in sorted(n for n, r in doc["images"].items() if r.get("colourway") == "target"):
        rec = doc["images"][name]
        photos.append({"id": Path(name).stem.split("-", 1)[1], "path": f"images/{name}", "view": rec["view_kind"],
                       "role": "target"})
    for w in cli.COLOURWAY_PHOTOS:
        photos.append({"id": w["id"], "path": f"images/{w['image']}", "view": doc["images"][w["image"]]["view_kind"],
                       "role": "colourway", "colour": w["colour"], "finish": w["finish"]})
    return cli.brief_text("Prada PRS 17WS 21D5J2 49M", photos, doc["images"])


@pytest.mark.skipif(not cli.BASIS_JSON.is_file(), reason="the pose basis (git-ignored data) is absent")
def test_brief_on_the_real_pose_basis():
    """The Prada job's brief from its fitted cameras: B1 shares 05's direction and 01 is its mirror (one direction:
    the review's one-view finding), B2 is 02's direction at 62 deg to the outer face (the moderate views that
    confirm a layout), the edge-on views, the number lines, the transported cameras' fit with their check against
    the starting model (the basis's model_check; B3 re-transported on its white-background matte, review of the
    build P1) and every pointer the instructions make to the brief."""
    brief = real_brief()
    assert "two temples): 01, 05, B1 | 02, B2 | 03 | 04, B3 | 06." in brief
    assert "square-on (under 30 deg): 01, 05, B1. At a moderate angle (30 to 75 deg): 02, B2. Edge-on (75 deg or " \
           "more): 03, 04, 06, B3." in brief
    assert "Outer-edge offsets: 03, 06. Outline line: 03." in brief and "Glossy photos: B1, B2, B3." in brief
    assert "No finish is given for 01, 02, 03, 04, 05, 06." in brief
    assert "- 02: angled; camera yaw +28.2, pitch +11.4; the charted temple's outer face at a moderate angle (62 deg;" \
           " points_px moves 1.91 mm per mm of depth error); camera fitted to the model's silhouette (all-views " \
           "fit): IoU 0.926" in brief
    assert "- 01: side; camera yaw -90.8, pitch +13.7; the mirrored temple's outer face, from its own side, " \
           "square-on (14 deg; points_px moves 0.24 mm per mm of depth error)" in brief
    assert "faces that point straight up at 30 deg from square-on; its compares print outer-edge offsets and the " \
           "outline line" in brief
    assert "camera moved from 02's by matching the two photos' silhouettes (photo to photo, on its registration " \
           "matte: IoU 0.992, mean contour 0.8 px), the temples' hinge opening as in 02; starting model's silhouette " \
           "on the compare's raster: IoU 0.934, temple IoU 0.875" in brief
    assert "camera moved from 04's by matching the two photos' silhouettes (photo to photo, on its registration " \
           "matte: IoU 0.992, mean contour 1.1 px), the temples' hinge opening as in 04; starting model's silhouette " \
           "on the compare's raster: IoU 0.977, temple IoU 0.886" in brief
    assert "not checked against the model" not in brief                  # every transported camera has its check
    for pointer in ("charted temple", "mirrored temple", "square-on", "at a moderate angle", "edge-on", "Directions",
                    "points_px moves", "Outer-edge offsets", "Outline line", "Glossy photos",
                    "faces that point straight up",
                    "revision 0", "glossy black", "fixed light, studio_dome_v1", "edge rounding"):
        assert pointer in brief, pointer                               # what the instructions say the brief gives
    assert "acetate" not in brief and "far more clearly" not in brief


# --------------------------------------------------------------------------------------------- estimate
def sample(tool, chars, images, status="ok", args=300):
    return {"tool": tool, "status": status, "text_chars": chars, "args_chars": args, "images": images}


def rehearsal(scale: int = 1) -> dict:
    cmp_img = [512, 400 * scale, "compare"]
    return {"first_request": {"text_chars": 30_000, "image_sizes": [[1226, 682]] * 6},
            "tool_outputs": [sample("facet_inspect", 1800, [[1024, 454, "chart"], [1022, 576, "sections"]]),
                             sample("photo_image", 300, [[1024, 570, "photo"]]),
                             sample("photo_compare", 1200, [cmp_img] * 3),
                             sample("facet_edit", 1400, [[1024, 454, "chart"], [1022, 576, "sections"]] + [cmp_img] * 5,
                                    status="applied", args=1500),
                             sample("facet_edit", 200, [], status="refused"),
                             sample("facet_variants", 700, [[1280, 899, "variants"]] * 2),
                             sample("facet_finish", 300, [], status="accepted")]}


def test_token_formula_and_steps():
    assert cli.image_tokens(1024, 454) == pytest.approx(0.975 * 32 * 15 + 112)
    step = cli.mix_step(rehearsal()["tool_outputs"], {"facet_edit": 1})
    edit = cli.call_tokens(rehearsal()["tool_outputs"][3])
    assert step["tokens"] == pytest.approx(edit["tokens"]) and step["results"] == 1.0    # refused edits are not priced
    assert edit["stubbable"] == pytest.approx(5 * cli.image_tokens(512, 400)) and edit["stub_images"] == 5
    with pytest.raises(ValueError, match="no successful"):
        cli.mix_step(rehearsal()["tool_outputs"], {"facet_restore": 1})


def test_estimate_table_guard_and_long_context():
    table = cli.facet_estimate(rehearsal())
    rows = {(r["requests"], r["effort"]): r for r in table["rows"]}
    assert set(rows) == {(n, e) for n in cli.RUN_LENGTHS for e in ("high", "max")}
    exp = [rows[(n, "max")]["expected"]["expected_micro_usd"] for n in cli.RUN_LENGTHS]
    assert exp == sorted(exp) and all(rows[k]["high"]["expected_micro_usd"] >= rows[k]["expected"]["expected_micro_usd"]
                                      for k in rows)
    assert rows[(40, "high")]["expected"] == rows[(40, "max")]["expected"]          # high is priced as max
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


def test_request_estimate_block(tmp_path):
    table = cli.facet_estimate(rehearsal())
    s = T.run_settings(effort="max", max_turns=None)
    block = cli.request_estimate(json.loads(json.dumps(table)), s, 30_000, [[1226, 682]] * 6)
    assert block["requests"] == 60 and block["text"].startswith("Illustrative 60-reply projection")
    assert int(block["suggested_cap_micro_usd"]) % 5_000_000 == 0 and "No approval" in block["limitation"]
    A.canonical_json(block)                                                           # plain JSON


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
        p = cli.LAB_DATA / pkg / "runs" / rid / "budget.json"
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
    sheets (its largest sheet repeated), and a request for a package whose calls can return more sheets than the
    estimate table priced is refused."""
    targets = [{"role": "target"}] * 6
    assert cli.variant_sheets(targets) == 3 and cli.variant_sheets(targets + [{"role": "reference"}]) == 3
    assert cli.variant_sheets(targets + [{"role": "colourway"}]) == 4 == cli.variant_sheets(
        targets + [{"role": "colourway"}] * 3)
    two = sample("facet_variants", 700, [[1280, 692, "variants"], [1280, 899, "variants"]])
    four = cli.priced_sample(two, 4)
    assert four["images"] == [[1280, 692, "variants"], [1280, 899, "variants"]] + [[1280, 899, "variants"]] * 2
    assert len(two["images"]) == 2 and cli.priced_sample(two, 2) is two      # the recorded call is not changed
    edit = rehearsal()["tool_outputs"][3]
    assert cli.priced_sample(edit, 4) is edit                                  # only facet_variants is padded
    t3, t4 = cli.facet_estimate(rehearsal(), sheets=3), cli.facet_estimate(rehearsal())
    assert (t3["variant_sheets"], t4["variant_sheets"]) == (3, 4)
    share = cli.MIXES["typical"]["facet_variants"] / sum(cli.MIXES["typical"].values())
    grow = t4["steps"]["typical"]["tokens"] - t3["steps"]["typical"]["tokens"]
    assert grow == pytest.approx(share * cli.image_tokens(1280, 899), abs=0.2)  # one more sheet, at its mix share
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


def test_main_requires_the_owner_settings(tmp_path):
    with pytest.raises(SystemExit):
        cli.main(["request", "--job", str(tmp_path)])
    with pytest.raises(SystemExit):
        cli.main(["estimate"])
    np.testing.assert_equal(cli.RUN_LENGTHS, (40, 60, 90))
