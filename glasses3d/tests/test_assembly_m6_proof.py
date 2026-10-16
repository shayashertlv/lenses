import json
from pathlib import Path

import pytest

from glasses3d.tests.proofs import assembly_m6_owner_edit as P


def test_scope_reference_is_exact_without_copying_or_expanding_weights():
    scope={"object_id":"O0001","target_ids":["F0001"],"vertex_weights":[{"vertex_id":1,"weight":.5}],
           "fixed_vertex_ids":[],"affected_polygon_ids":[0],"normal_affected_polygon_ids":[0,1]}
    op={"id":"one","kind":"translate_region","target_ids":["F0001"],"scope":scope,"params":{"delta_mm":[.1,0,0]}}
    out=P.concise_operation(op,scope)
    assert out["scope_ref_sha256"] == P.G.digest(scope)
    assert "scope" not in out and out["target_ids"]==["F0001"]
    out["params"]["delta_mm"][0]=9
    assert op["params"]["delta_mm"][0] == .1


def test_scripted_explanation_units_are_readable_without_altering_owner_words():
    assert P.readable_units("by0.15mm within2mm, zero8mm") == "by 0.15 mm within 2 mm, zero 8 mm"
    assert "boarder" in P.REQUEST and "facet ." in P.REQUEST


def test_proof_edit_routes_through_actual_specialist_and_two_reviews(tmp_path, monkeypatch):
    child = tmp_path / "work/temple_stage" / P.hashlib.sha256(b"proof-owner-stage").hexdigest()[:16]

    def confirmed_operations(authority, operation_ids=None, current_head=None):
        assert authority.root == child
        return {"operations": [{"id": "selected-2"}, {"id": "selected-1"}]}

    monkeypatch.setattr(P.edits.Authority, "resolve", confirmed_operations)
    s=P.Script(tmp_path,{"operations":[{"id":"one"}]},"edit",tmp_path)
    assert s.edit(0)[0]=="temple_stage"
    assert s.edit(1)[0]=="facet_owner_edit"
    assert s.edit(1)[1]=={"operation_ids":["selected-2","selected-1"],"corrections":None}
    assert s.edit(2)[0]=="edit_report" and s.edit(3)[0]=="finish"
    assert s.edit(5)[0]=="edit_report" and s.edit(6)[0]=="finish"
    assert s.edit(1)[3].is_relative_to(tmp_path/"work/temple_stage")


def test_proof_edit_requires_confirmed_child_authority(tmp_path):
    s=P.Script(tmp_path,{"operations":[{"id":"one"}]},"edit",tmp_path)
    with pytest.raises(ValueError, match="owner confirmation is required"):
        s.edit(1)


def test_source_inventory_never_reads_credential_or_launch_specs(tmp_path):
    (tmp_path/"job.json").write_text("{}")
    (tmp_path/"inputs").mkdir()
    (tmp_path/"inputs/.env").write_text("must not read")
    (tmp_path/"inputs/test.spec.json").write_text("must not read")
    (tmp_path/"inputs/seed.blend").write_bytes(b"model")
    assert set(P.protected(tmp_path))=={"job.json","inputs/seed.blend"}
