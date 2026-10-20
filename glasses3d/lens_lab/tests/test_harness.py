import copy
from pathlib import Path

import pytest

from glasses3d.lens_lab import harness as H


def fixture_state():
    state = {key: {"fixed": "digest"} for key in H.PROTECTED_KEYS}
    state.update(materials={"lens": "old", "frame": "fixed"}, node_groups={"shared": "group"},
                 images={"frame-print": "image"})
    deps = {"lens": {"node_groups": ["shared"], "images": []},
            "other": {"node_groups": ["shared"], "images": ["frame-print"]}}
    return state, deps


def test_scope_allows_only_lens_material_and_private_dependencies():
    source, deps = fixture_state()
    after = copy.deepcopy(source)
    after["materials"]["lens"] = "new"
    after["node_groups"]["new-film"] = "new"
    after["images"]["film-texture"] = "new"
    current = copy.deepcopy(deps)
    current["lens"]["node_groups"].append("new-film")
    current["lens"]["images"].append("film-texture")
    assert H.scope_violations(source, after, ["lens"], deps, current) == []


@pytest.mark.parametrize("category", H.PROTECTED_KEYS)
def test_geometry_transforms_uv_normals_roles_scene_are_frozen(category):
    source, deps = fixture_state()
    after = copy.deepcopy(source)
    after[category] = "changed"
    assert category in H.scope_violations(source, after, ["lens"], deps, deps)


@pytest.mark.parametrize("category,name", [
    ("materials", "frame"), ("node_groups", "shared"), ("images", "frame-print"),
    ("node_groups", "unused"), ("images", "unassigned"),
])
def test_non_lens_and_shared_assets_refuse(category, name):
    source, deps = fixture_state()
    after = copy.deepcopy(source)
    after[category][name] = "changed"
    assert H.scope_violations(source, after, ["lens"], deps, deps)


def test_material_slot_substitution_refuses():
    source, deps = fixture_state()
    after = copy.deepcopy(source)
    after["materials"]["replacement"] = after["materials"].pop("lens")
    assert "material membership" in H.scope_violations(source, after, ["lens"], deps, deps)


@pytest.mark.parametrize("consumer", ["world", "compositor", "texture", "geometry_group", "camera", "empty"])
def test_shared_non_material_images_are_not_lens_exclusive(consumer, capsys):
    import json
    from types import SimpleNamespace as NS
    image = NS(name="shared-image", pixels=[])
    image_node = NS(type="TEX_IMAGE", image=image)
    other_node = NS(type="TEX_ENVIRONMENT", image=image)
    tree = NS(name="other-tree", nodes=[other_node])
    data = NS(materials=[NS(name="lens", node_tree=NS(nodes=[image_node]))],
              worlds=[], lights=[], scenes=[], textures=[], node_groups=[], objects=[], cameras=[])
    if consumer == "world":
        data.worlds = [NS(node_tree=tree)]
    elif consumer == "compositor":
        data.scenes = [NS(compositing_node_group=tree)]
    elif consumer == "texture":
        data.textures = [NS(image=image)]
    elif consumer == "geometry_group":
        data.node_groups = [tree]
    elif consumer == "camera":
        data.cameras = [NS(background_images=[NS(image=image)])]
    else:
        data.objects = [NS(type="EMPTY", data=image)]
    exec(H.dependency_code(["lens"]), {"bpy": NS(data=data)})
    deps = json.loads(capsys.readouterr().out)
    assert deps["lens"]["images"] == ["shared-image"]
    assert deps["other"]["images"] == ["shared-image"]
    before, _ = fixture_state()
    before["images"]["shared-image"] = "before"
    after = copy.deepcopy(before)
    after["images"]["shared-image"] = "after"
    assert "images:shared-image" in H.scope_violations(before, after, ["lens"], deps, deps)


def test_heavy_work_requires_exit_line(tmp_path):
    status = tmp_path / "status.txt"
    status.write_text("start 2026-10-20\nwaiting to exit\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="waits"):
        H.ensure_heavy_allowed(status)
    status.write_text("start 2026-10-20\nexit 0 2026-10-20\n", encoding="utf-8")
    assert H.ensure_heavy_allowed(status)["sha256"] == H.sha256(status)


def test_original_job_cannot_be_trial():
    with pytest.raises(ValueError, match="private subdirectory"):
        H.trial_path(H.SOURCE_JOB)
    with pytest.raises(ValueError):
        H.trial_path(H.DATA)


def test_manifest_cannot_redirect_worker_to_original(monkeypatch, tmp_path):
    import json
    monkeypatch.setattr(H, "DATA", tmp_path)
    private = tmp_path / "trial"
    private.mkdir()
    (private / "trial.json").write_text(json.dumps({"job_dir": str(H.SOURCE_JOB)}))
    with pytest.raises(ValueError, match="private job"):
        H.LabSession(private)


def test_source_fingerprint_constants_are_full_sha256():
    assert len(H.SOURCE_BLEND_SHA) == len(H.SOURCE_GLB_SHA) == 64


def test_laser_mark_is_not_reset_or_editable():
    glass = {"name": "shield", "extras": {"partRole": "lens"},
             "extensions": {"KHR_materials_transmission": {"transmissionFactor": .94}}}
    mark = {"name": "mark", "extras": {"partRole": "lens"}}
    crystal = {**glass, "name": "crystal frame", "extras": {"partRole": "frame"}}
    assert H.glass_material_names({"materials": [glass, mark, crystal]}) == ["shield"]


def test_main_suite_collection_scope_is_disjoint():
    # The production command explicitly collects glasses3d/tests. This package
    # has its own config and no imports or collection hooks in that directory.
    assert not Path(__file__).resolve().is_relative_to(H.REPO / "glasses3d/tests")
    config = (H.REPO / "glasses3d/lens_lab/pytest.ini").read_text()
    assert "testpaths = tests" in config
