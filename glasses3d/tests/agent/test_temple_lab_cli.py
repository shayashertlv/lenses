"""The facet lab's package entry points (agent/temple_lab/cli.py; plan/WP6_FACETS.md §4.3-4.4): prepare with fakes
(no Blender), the clean-package and binding refusals of make_spec, the approval request's settings binding, the
instructions' contract and the facet-run estimate. The real-Blender path is T5 (tests/proofs/astra_temple_lab.py).
"""
import hashlib
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

pytest.importorskip("agents")
from glasses3d.agent import approvals as A  # noqa: E402
from glasses3d.agent import loop as L  # noqa: E402
from glasses3d.agent.temple_lab import cli  # noqa: E402
from glasses3d.agent.temple_lab import state as S  # noqa: E402
from glasses3d.agent.temple_lab import tools as T  # noqa: E402
from glasses3d.agent.temple_lab.lab import LAB_SCHEMA, Lab  # noqa: E402
from glasses3d.g3d.temple import fixtures as FX  # noqa: E402
from glasses3d.tests.agent.test_temple_lab import FakeBasis, FakeSeeing, FakeSession  # noqa: E402

KINDS = {"01": ("side", -90.8), "02": ("angled", 30.0), "03": ("top", 0.0), "04": ("front", 0.0),
         "05": ("side", 90.6), "06": ("rear", 180.0)}


def sha(p) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def sources(tmp: Path) -> dict:
    """A fake original scene, reference folder (manifest + 6 target photos + a variant photo) and pose basis."""
    ref = tmp / "reference"
    ref.mkdir(parents=True)
    scene = tmp / "scene.blend"
    scene.write_bytes(b"BLENDER fake scene")
    files, images = {}, {}
    for k, (pid, (kind, yaw)) in enumerate(KINDS.items()):
        name = f"reference-{pid}.png"
        Image.new("RGB", (64 + 8 * k, 40), (40 * k, 120, 200)).save(ref / name)
        files[name] = {"sha256": sha(ref / name), "label": kind}
        images[name] = {"image": str(ref / name), "image_sha256": files[name]["sha256"], "view_kind": kind,
                        "colourway": "target", "camera": {"yaw": yaw}}
    Image.new("RGB", (30, 30), (0, 0, 0)).save(ref / "black-side.png")
    images["black-side.png"] = {"image": str(ref / "black-side.png"), "image_sha256": sha(ref / "black-side.png"),
                                "view_kind": "side", "colourway": "variant", "camera": {"yaw": 90.0}}
    (ref / "manifest.json").write_text(json.dumps({"name": "Test Frame 1", "files": files}), encoding="utf-8")
    basis = tmp / "cameras.json"
    basis.write_text(json.dumps({"model": {"skip": []}, "images": images}), encoding="utf-8")
    return {"scene": scene, "scene_sha256": sha(scene), "reference": ref, "basis_json": basis}


def fake_import(root, cameras_json, photos, *, reason):
    d = Path(root) / "cameras" / "basis-01"
    d.mkdir(parents=True)
    (d / "cameras.json").write_bytes(Path(cameras_json).read_bytes())
    (d / "basis.json").write_text(json.dumps({"id": "basis-01", "photos": sorted(photos), "reason": reason}),
                                  encoding="utf-8")
    return SimpleNamespace(id="basis-01")


class PrepLab:
    """Lab.prepare's outward behaviour: the photo_env light frozen in work/compare, revision 0, a verified head."""
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
        p = self.root / "work" / "compare" / "env-basis-01.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"weights_sha": "w" * 16}), encoding="utf-8")
        return {"status": "prepared", "checkpoint": "c0001"}

    def is_clean(self):
        return True

    def photo_ids(self):
        return [p["id"] for p in self.manifest["photos"]]

    def exit_checkpoint(self):
        return {"id": "c0001", "sha256": "0" * 64, "verified": True}

    def close(self):
        self.closed = True


def package(tmp_path: Path, rig: str = "photo_env") -> tuple[Path, dict]:
    src = sources(tmp_path)
    root = tmp_path / "job"
    out = cli.prepare(root, rig=rig, source_scene=src["scene"], scene_sha256=src["scene_sha256"],
                      reference=src["reference"], basis_json=src["basis_json"], lab_class=PrepLab,
                      import_basis=fake_import)
    return root, out


def lab_on(root: Path) -> Lab:
    """A real Lab on the prepared package with a fake scene and compare (revision 0 = prada_default, applied and
    compared by the prepare step), as test_temple_lab.make_lab does."""
    s = FakeSession(root)
    lab = Lab(root, session=s, seeing=FakeSeeing(s, root))
    lab._basis = FakeBasis()
    lab.state["start_checkpoint"], lab.state["start_fingerprint"] = "c0000", "fp-original"
    lab.state["original"] = {"footprints": {"decals": [], "hinge": []}, "chart_mesh": None}
    rid, _ = lab._register(FX.prada_default(), parent=None, kind="template", label="revision 0")
    assert lab._apply("prepare-r0", rid, tool="prepare")["status"] == "ok"
    assert lab._compare("prepare-r0", lab.photo_ids(), by="prepare")["status"] == "ok"
    lab.save()
    return lab


# --------------------------------------------------------------------------------------------- prepare
def test_prepare_writes_an_isolated_bound_package(tmp_path):
    root, out = package(tmp_path)
    assert out["status"] == "prepared" and out["photos"] == 6 and out["paid_calls"] == 0 and PrepLab.made[-1].closed
    inputs = root / "inputs"
    images = sorted(p.name for p in (inputs / "images").iterdir())
    assert images == [f"reference-0{k}.png" for k in range(1, 7)]                      # target photos only
    assert sorted(p.name for p in inputs.iterdir()) == ["brief.txt", "images", "instructions.txt", "lab.json",
                                                         "scene.blend"]
    assert sha(inputs / "scene.blend") == sha(tmp_path / "scene.blend")
    lab_json = json.loads((inputs / "lab.json").read_text(encoding="utf-8"))
    assert lab_json["schema"] == LAB_SCHEMA and lab_json["rig"] == "photo_env"
    assert {a["path"]: a["sha256"] for a in lab_json["assets"]}["images/reference-05.png"] == \
        sha(inputs / "images/reference-05.png")
    assert [p["id"] for p in lab_json["photos"]] == ["01", "02", "03", "04", "05", "06"]
    basis = json.loads((root / "basis" / "cameras.json").read_text(encoding="utf-8"))
    assert sorted(basis["images"]) == [f"reference-0{k}.png" for k in range(1, 7)]   # the variant photo is left out
    assert set(lab_json["basis"]["files"]) == {"basis/cameras.json", "cameras/basis-01/cameras.json",
                                               "cameras/basis-01/basis.json"}
    job = json.loads((root / "job.json").read_text(encoding="utf-8"))
    assert job["implementation"] == cli.implementation() and job["rig"] == "photo_env"
    assert job["environment"]["weights_sha"] == "w" * 16
    assert [t["name"] for t in json.loads((root / "tools.json").read_text(encoding="utf-8"))] == list(T.TOOL_NAMES)
    prepared = json.loads((root / "prepared.json").read_text(encoding="utf-8"))
    assert prepared["checkpoint"] == "c0001" and prepared["revision0"]["sha256"] == "a" * 64
    brief = (inputs / "brief.txt").read_text(encoding="utf-8")
    assert "Test Frame 1" in brief and "- 05: side; it shows the charted temple" in brief
    assert "- 01: side; it shows the mirrored temple" in brief
    assert (inputs / "instructions.txt").read_bytes() == (cli.HERE / "instructions.txt").read_bytes()


def test_prepare_refusals(tmp_path):
    src = sources(tmp_path)
    kw = dict(source_scene=src["scene"], reference=src["reference"], basis_json=src["basis_json"],
              lab_class=PrepLab, import_basis=fake_import)
    with pytest.raises(ValueError, match="expected sha256"):
        cli.prepare(tmp_path / "a", scene_sha256="0" * 64, **kw)
    with pytest.raises(ValueError, match="rig"):
        cli.prepare(tmp_path / "b", rig="photo_v1", scene_sha256=src["scene_sha256"], **kw)
    (tmp_path / "c").mkdir()
    with pytest.raises(ValueError, match="new empty"):
        cli.prepare(tmp_path / "c", scene_sha256=src["scene_sha256"], **kw)
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
    assert [p.label for p in spec.photos] == [f"TARGET PHOTO 0{k} ({k} of 6)" for k in range(1, 7)]
    assert [r["path"] for r in req["run_inputs"]["photos"]] == [f"images/reference-0{k}.png" for k in range(1, 7)]
    assert spec.contract.named == frozenset(T.TOOL_NAMES) and "c0001" in spec.scene_note
    # the loop's own re-check of the bound request agrees (settings included)
    assert L._check_bound_request(run, spec)["tool_registry_sha256"] == req["tool_registry_sha256"]


def test_make_spec_refuses_drift_and_a_used_package(tmp_path, monkeypatch):
    root, _ = package(tmp_path)
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
    photo = root / "inputs" / "images" / "reference-02.png"
    saved = photo.read_bytes()
    photo.write_bytes(saved + b"x")
    with pytest.raises(ValueError, match="input bytes changed"):
        spec_of(lab)
    photo.write_bytes(saved)
    lab._compare("call-1", ["05"], by="astra")                        # a facet tool ran: not clean any more
    with pytest.raises(ValueError, match="clean package"):
        spec_of(lab)


# --------------------------------------------------------------------------------------------- instructions
def test_instructions_contract_and_finish_text(tmp_path):
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
    assert pre in " ".join(text.split())                               # the preconditions verbatim, K = 4
    low = text.lower()
    for banned in ("wp6", "inspect every image", "again before concluding", "not been established", "failed",
                   "owner", "earlier run", "v1"):
        assert banned not in low, banned
    for needed in ("points_px", "E_CREASE_SHAPE", "E_JUDGEMENT", "E_BUSY", "t_min is 1.0", "station table",
                   "qualitative", "start model", "fade", "roll_mm", "+-1 mm", "largest"):
        assert needed in text, needed


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


def test_main_requires_the_owner_settings(tmp_path):
    with pytest.raises(SystemExit):
        cli.main(["request", "--job", str(tmp_path)])
    with pytest.raises(SystemExit):
        cli.main(["estimate"])
    np.testing.assert_equal(cli.RUN_LENGTHS, (40, 60, 90))
