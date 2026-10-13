"""Generic fitted photo routing keeps canonical views and confined output identities distinct."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from glasses3d.agent.seeing import NAME_RE, Seeing, _safe_view_names
from glasses3d.g3d_host import blender_camera as BC
from glasses3d.g3d_host import compare as C
from glasses3d.g3d_host.camera import Camera, NormFrame


class Session:
    def __init__(self, root):
        self.root = root
        self.head = {"id": "cp-001", "fingerprint": "a" * 64}
        self.calls, self.records = [], []

    def new_call_id(self, tool, call_id=None):
        return call_id or f"{tool}-{len(self.records)}"

    def record_call(self, *args):
        self.records.append(args)

    def call_read(self, tool, method, params, **kwargs):
        self.calls.append((method, params))
        if method == "pick":
            return {"status": "ok", "result": {"hits": [{"hit": False} for _ in params["rays"]],
                                                "origin_mm": [0, 0, 0]}}
        assert method == "render"
        views, paths = [], set()
        for v in params["views"]:
            assert NAME_RE.fullmatch(v["name"])
            assert v["name"].strip("._") == v["name"]
            files = {}
            for ps in v["passes"]:
                path = Path(params["out_dir"]) / f"{v['name']}_{ps}.png"
                assert str(path).casefold() not in paths
                paths.add(str(path).casefold())
                path.parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGBA", v["camera"]["resolution"], (64, 96, 128, 255)).save(path)
                files[ps] = {"path": str(path), "sha256": C.sha256_file(path), "engine": "test"}
            views.append({"name": v["name"], "files": files})
        return {"status": "ok", "result": {"views": views, "engine_final": "test", "gpu": None,
                                             "fallback": None, "fingerprint_before": self.head["fingerprint"],
                                             "seconds": 0}}


@pytest.fixture
def routing(tmp_path, monkeypatch):
    names = ["front", "owner photo", "owner_photo", "owner.photo", "角度", "角 度", "reference-01"]
    cams = {name: Camera(15 + i * 10, 7, 2, 0, 200, 79.5, 59.5) for i, name in enumerate(names)}
    basis = SimpleNamespace(id="basis-01", refs=dict.fromkeys(names), frame=NormFrame((0, 0, 0), 200),
                            pivot_mm=np.zeros(3), camera=cams.__getitem__, shape=lambda _: (120, 160),
                            colourway=lambda _: "target", is_usable=lambda rid: rid in names,
                            usable_refs=lambda: list(names), unusable_refs=lambda: {})
    monkeypatch.setattr(C, "load_basis", lambda *_: basis)
    s = Session(tmp_path)
    see = Seeing(s)
    see._bounds = (s.head["fingerprint"], {"origin_mm": [0, 0, 0], "lo": [-70, -25, -140],
                                        "hi": [70, 25, 0], "objects": {
                                            "model": {"lo": [-70, -25, -140], "hi": [70, 25, 0]}}})
    return see, s, basis


def test_all_reference_names_route_to_the_fitted_camera(routing):
    see, _s, basis = routing
    refs = see.resolve_views(["reference"], 256, call_id="test")
    assert {r["ref"] for r in refs} == set(basis.refs)
    assert len({r["name"].casefold() for r in refs}) == len(basis.refs)
    for r in refs:
        assert r["kind"] == "reference" and r["camera"] == basis.camera(r["ref"])
        assert r["basis"] == basis.id and NAME_RE.fullmatch(r["name"])
        assert r["name"] not in BC.CANONICAL_VIEWS
    assert next(r for r in refs if r["ref"] == "reference-01")["name"] == "reference-01"
    all_views = see.resolve_views(["all"], 256, call_id="test")
    assert len(all_views) == len(BC.CANONICAL_VIEWS) + len(basis.refs)
    assert next(r for r in all_views if r["name"] == "front")["kind"] == "canonical"


@pytest.mark.parametrize("selector,rid", [("owner photo.png", "owner photo"), ("owner photo", "owner photo"),
                                         ("owner.photo", "owner.photo"), ("owner.photo.png", "owner.photo"),
                                         ("front.png", "front"), ("reference:front", "front"),
                                         ("reference:front.png", "front"), ("角度.png", "角度")])
def test_photo_id_or_filename_is_accepted_for_render_and_pick(routing, selector, rid):
    see, s, basis = routing
    view = see.resolve_views([selector], 256, call_id="test")[0]
    assert view["ref"] == rid and view["camera"] == basis.camera(rid)
    points = [[40, 60], [79.5, 59.5]]
    result = see.pick_surface_point(selector, points, size_px=256)
    assert result["status"] == "ok", result
    expected = BC.to_blender(basis.camera(rid), basis.frame, (0, 0, 0), basis.shape(rid))
    ro, rd = BC.rays(expected, points)
    rays = s.calls[-1][1]["rays"]
    np.testing.assert_allclose([r["origin"] for r in rays], ro)
    np.testing.assert_allclose([r["direction"] for r in rays], rd)


def test_canonical_name_retains_canonical_meaning(routing):
    see, _s, basis = routing
    view = see.resolve_views(["front"], 256, call_id="test")[0]
    assert view["kind"] == "canonical" and view["camera"].yaw == 0
    assert view["camera"] != basis.camera("front")


def test_render_manifest_retains_photo_identity_and_fitted_camera(routing):
    see, _s, basis = routing
    result = see.render_views(["reference"], passes=["native", "silhouette"], size_px=256)
    assert result["status"] == "ok", result
    manifest = json.loads(Path(result["manifest"]).read_text())
    assert {r["ref"] for r in result["views"].values()} == set(basis.refs)
    paths = []
    for alias, view in manifest["views"].items():
        assert view["camera"] == basis.camera(view["ref"]).to_dict()
        assert view["kind"] == "reference" and view["basis"] == basis.id
        assert result["views"][alias]["ref"] == view["ref"]
        for f in view["files"].values():
            path = Path(f["path"])
            assert path.is_file() and path.is_relative_to(see.work)
            paths.append(str(path).casefold())
    assert len(paths) == len(set(paths))


def test_render_boundary_keeps_original_compare_names_and_unique_paths(routing, tmp_path):
    see, _s, basis = routing
    names = ["owner photo", "owner_photo", "角度", "角 度", "UPPER", "upper", ".name", "x" * 90]
    cam = BC.to_blender(basis.camera("front"), basis.frame, (0, 0, 0), (120, 160))
    views = [{"name": name, "blender": cam, "worker_passes": ["native"]} for name in names]
    result = see._render("compare_to_photos", "compare-test", views, tmp_path / "work" / "compare")
    assert [v["name"] for v in result["result"]["views"]] == names
    paths = [v["files"]["native"]["path"].casefold() for v in result["result"]["views"]]
    assert len(paths) == len(set(paths))


def test_generated_alias_cannot_overwrite_an_existing_photo_identity():
    name = "owner photo"
    digest_name = "ref-" + hashlib.sha256(name.encode()).hexdigest()[:24]
    names = [name, digest_name, "front"]
    aliases = _safe_view_names(names, reserved=BC.CANONICAL_VIEWS)
    assert len(set(aliases.values())) == len(names)
    assert aliases[digest_name] == digest_name and aliases["front"] != "front"
    assert all(NAME_RE.fullmatch(n) for n in aliases.values())


@pytest.mark.parametrize("selector", ["reference:../owner photo.png", "reference:C:\\photo.png",
                                     "../owner photo.png", {"name": "../custom", "yaw": 0, "pitch": 0}])
def test_reference_routing_does_not_allow_paths_or_unsafe_custom_names(routing, selector):
    see, s, _basis = routing
    result = see.render_views([selector], passes=["native"], size_px=256)
    assert result["status"] == "error" and result["code"] == "E_ARGS", result
    assert not s.calls


def test_compare_photo_ids_with_dots_are_not_stripped_twice(routing):
    _see, _s, basis = routing
    assert Seeing._ref_ids(basis, ["owner.photo", "owner.photo.png", "owner photo.png"]) == [
        "owner.photo", "owner photo"]
