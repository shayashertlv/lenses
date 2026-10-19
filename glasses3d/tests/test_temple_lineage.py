"""Names survive a builder upgrade only through authenticated producer lineage."""
import copy
import json

import pytest

from glasses3d.g3d import temple_lineage as L
from glasses3d.g3d.temple import build_temple, fixtures


@pytest.fixture(scope="module")
def built():
    return build_temple(fixtures.prada_default())


def inputs(tb, side=1):
    v, polygons, _ = tb.world(side)
    raw = {"vertices_local": v.tolist(), "polygons": polygons}
    metadata = {"canonical_spec": tb.spec.canonical, "spec_sha256": tb.spec.sha256,
                "side": "+x" if side > 0 else "-x"}
    return metadata, raw


@pytest.mark.parametrize("side", [-1, 1])
def test_frozen_names_use_current_vertices_and_never_rebuild(built, monkeypatch, side):
    metadata, raw = inputs(built, side)
    metadata["lineage"] = L.freeze(built, side, len(raw["vertices_local"]), raw["polygons"])
    expected = L.aliases(metadata, raw)
    monkeypatch.setattr("glasses3d.g3d.temple.build.build_temple",
                        lambda *_: pytest.fail("a future builder must not read a frozen delivery"))
    raw["vertices_local"][0] = [999., -888., 777.]
    assert L.aliases(metadata, raw) == expected
    assert expected["facets"] and expected["points"]
    assert expected["historical_coordinates_used"] is False


@pytest.mark.parametrize("change", ["vertex_count", "face_order", "winding", "extra_face", "side", "spec", "digest"])
def test_corrupt_or_other_mesh_lineage_refuses(built, change):
    metadata, raw = inputs(built)
    metadata = copy.deepcopy(metadata)
    metadata["lineage"] = L.freeze(built, 1, len(raw["vertices_local"]), raw["polygons"])
    if change == "vertex_count": raw["vertices_local"].append([0, 0, 0])
    elif change == "face_order": raw["polygons"][:2] = raw["polygons"][:2][::-1]
    elif change == "winding": raw["polygons"][0] = raw["polygons"][0][::-1]
    elif change == "extra_face": raw["polygons"].append(raw["polygons"][0])
    elif change == "side": metadata["side"] = "-x"
    elif change == "spec": metadata["canonical_spec"]["hinge"]["z_mm"] += 1
    else: metadata["lineage"]["facets"].clear()
    with pytest.raises(ValueError): L.aliases(metadata, raw)


def test_no_producer_evidence_refuses_even_if_current_builder_happens_to_match(built, tmp_path):
    metadata, raw = inputs(built)
    with pytest.raises(ValueError, match="No authenticated producer"):
        L.aliases(metadata, raw, tmp_path)


def test_authenticated_old_builder_survives_different_current_topology(built, monkeypatch, tmp_path):
    from glasses3d.g3d import temple_archive
    metadata, raw = inputs(built)
    # A wrong count/topology models an upgraded producer. The only usable source is
    # the archived build; this fixture does not make guesses from current positions.
    different = copy.copy(built)
    different.V = built.V[:-1]
    monkeypatch.setattr("glasses3d.g3d.temple.build.build_temple", lambda _: different)
    monkeypatch.setattr(temple_archive, "archived_builders", lambda *a, **k: iter([
        (lambda _: built, {"archive_sha256": "a" * 64})]))
    result = L.aliases(metadata, raw, tmp_path)
    assert result["facets"] == L.freeze(built, 1, len(built.V), raw["polygons"])["facets"]
    assert result["lineage_provenance"] == [{"archive_sha256": "a" * 64}]
    raw["polygons"][0] = raw["polygons"][0][::-1]
    with pytest.raises(ValueError, match="no guessed aliases"):
        L.aliases(metadata, raw, tmp_path)


def test_bad_frozen_record_cannot_fall_back_to_archive(built, monkeypatch, tmp_path):
    from glasses3d.g3d import temple_archive
    metadata, raw = inputs(built)
    metadata["lineage"] = {}
    monkeypatch.setattr(temple_archive, "archived_builders", lambda *a, **k: pytest.fail("must refuse damaged authority"))
    with pytest.raises(ValueError, match="digest"):
        L.aliases(metadata, raw, tmp_path)


def test_conflicting_authenticated_names_refuse(built, monkeypatch, tmp_path):
    from glasses3d.g3d import temple_archive
    metadata, raw = inputs(built)
    alternate = copy.copy(built)
    alternate.poly_region = list(built.poly_region)
    pi = next(i for i, kind in enumerate(built.poly_kind) if kind in ("outer", "roll"))
    alternate.poly_region[pi] = "different_name"
    monkeypatch.setattr(temple_archive, "archived_builders", lambda *a, **k: iter([
        (lambda _: built, {"archive_sha256": "a" * 64}),
        (lambda _: alternate, {"archive_sha256": "b" * 64})]))
    with pytest.raises(ValueError, match="aliases disagree"):
        L.aliases(metadata, raw, tmp_path)


@pytest.mark.parametrize("side", [-1, 1])
def test_checked_triangles_retain_exact_parent_facet_names(built, side):
    metadata, raw = inputs(built, side)
    _, polygons, triangles = built.world(side)
    pi = next(i for i, p in enumerate(polygons) if len(p) > 3)
    pieces = triangles[built.tri_poly == pi].tolist()
    raw["polygons"] = polygons[:pi] + pieces + polygons[pi + 1:]
    record = L.freeze(built, side, len(built.V), raw["polygons"])
    assert L.verified(record, metadata, raw)["status"] == "verified_topology"
    # Same triangles in a different order are not the stored producer's lineage.
    raw["polygons"] = polygons[:pi] + pieces[::-1] + polygons[pi + 1:]
    with pytest.raises(ValueError): L.freeze(built, side, len(built.V), raw["polygons"])


def test_writer_can_record_explicit_uv_split_lineage(built):
    metadata, raw = inputs(built)
    pi = next(i for i, kind in enumerate(built.poly_kind) if kind == "outer")
    a, b, c = raw["polygons"][pi][:3]
    new_vertex = len(raw["vertices_local"])
    raw["vertices_local"].append([0, 0, 0])
    raw["polygons"][pi:pi + 1] = [[a, b, new_vertex], [a, new_vertex, c]]
    sources = list(range(pi)) + [pi, pi] + list(range(pi + 1, len(built.polys)))
    record = L.freeze(built, 1, new_vertex + 1, raw["polygons"], sources=sources)
    result = L.verified(json.loads(json.dumps(record)), metadata, raw)
    name = built.poly_region[pi].split("/")[0]
    assert pi in result["facets"][name] and pi + 1 in result["facets"][name]
    with pytest.raises(ValueError): L.freeze(built, 1, new_vertex + 1, raw["polygons"])


@pytest.mark.parametrize("field,value", [("facets", {"bad": [-1]}), ("facets", {"bad": [True]}),
                                        ("points", {"bad": -1}), ("points", {"bad": True})])
def test_even_self_consistent_bad_indices_refuse(built, field, value):
    metadata, raw = inputs(built)
    record = L.freeze(built, 1, len(built.V), raw["polygons"])
    record[field] = value
    record["sha256"] = L.digest({key: val for key, val in record.items() if key != "sha256"})
    with pytest.raises(ValueError, match="indices"): L.verified(record, metadata, raw)


@pytest.mark.parametrize("field", ["options", "hinge"])
def test_frozen_constraints_cannot_override_hashed_spec(built, field):
    metadata, raw = inputs(built)
    record = L.freeze(built, 1, len(built.V), raw["polygons"])
    record[field] = {**record[field], "t_min_mm" if field == "options" else "z_mm": 999}
    record["sha256"] = L.digest({key: val for key, val in record.items() if key != "sha256"})
    with pytest.raises(ValueError, match="differ"): L.verified(record, metadata, raw)


def test_worker_context_authenticates_before_armed_calls_and_clears(built, monkeypatch, tmp_path):
    from glasses3d.g3d import temple_archive
    metadata, raw = inputs(built)
    monkeypatch.setattr(temple_archive, "archived_builders", lambda *a, **k: iter([
        (lambda _: built, {"archive_sha256": "a" * 64})]))
    with L.alias_context(tmp_path):
        monkeypatch.setattr(temple_archive, "archived_builders", lambda *a, **k: pytest.fail("armed I/O"))
        assert L.aliases(metadata, raw)["status"] == "verified_topology"
    with pytest.raises(ValueError, match="No authenticated producer"):
        L.aliases(metadata, raw)
