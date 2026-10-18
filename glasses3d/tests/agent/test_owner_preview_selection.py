"""Offline confirmation binds a delivered amount choice, never a fresh agent proposal."""
from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from glasses3d.agent import cli, edits
from glasses3d.agent.journal import CheckpointIndex
from glasses3d.agent.tools import owner_preview
from .test_owner_edit_lifecycle_review import selected_source, _write


@pytest.fixture
def shown_choices(selected_source, monkeypatch):
    f = selected_source
    first = edits.create_understand(f.source, "Move this face.", destination=f.destination)
    checkpoint = first / "checkpoints/c0000.blend"
    checkpoint.parent.mkdir()
    checkpoint.write_bytes((first / "inputs/seed.blend").read_bytes())
    head = {**f.chosen, "id":"c0000", "file":checkpoint.name}
    CheckpointIndex(checkpoint.parent).append(head)
    snapshot = first / "work/model_maps/example/snapshot.json"
    _write(snapshot, {"fingerprint":f.chosen["fingerprint"]})
    image = first / "work/pv-assets/choice.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"delivered option pixels")
    base = [{"id":"move", "kind":"translate_region", "target_ids":["face"],
             "scope":{"vertex_weights":[{"vertex_id":0,"weight":1}]}, "params":{"delta_mm":[.2,0,0]}}]
    options = [{"id":key,"label":label,"scale":scale,
                "operations":owner_preview.scaled_operations(base,scale),"export":{"fingerprint":key*64}}
               for key,label,scale in (("A","Half",.5),("B","Full",1.0))]
    evidence = {p.relative_to(first).as_posix():edits.sha(p) for p in (snapshot,image)}
    preview = {"schema":owner_preview.SCHEMA,"status":"ok","call_id":"preview",
               "source_fingerprint":f.chosen["fingerprint"],"source_head":head,
               "source_checkpoint_sha256":head["sha256"], "map_id":"example","map_sha256":"a"*64,
               "base_operations":base,"options":options,"evidence_files":evidence,"capture_receipts":[],
               "preview_receipt":"work/pv/example/preview.json"}
    path = first / preview["preview_receipt"]
    _write(path,preview)
    _write(path.with_name("preview-delivery.json"), {"schema":"g3d.owner_amount_preview_delivery/1",
        "preview_sha256":edits.sha(path),"call_id":"preview","delivered":True})
    artifact = {"schema":edits.UNDERSTANDING_SCHEMA,"status":"awaiting_owner_confirmation",
                "call_id":"understand", "source_fingerprint":f.chosen["fingerprint"],
                "map_id":"example","map_sha256":"a"*64,"operations":base,
                "snapshot_path":snapshot.relative_to(first).as_posix(),
                "evidence_files":{**evidence,preview["preview_receipt"]:edits.sha(path)},
                "amount_preview":{"preview_receipt":preview["preview_receipt"],
                                  "preview_sha256":edits.sha(path),"call_id":"preview","options":options}}
    artifact_path = first / "work/owner_edit/understanding.json"
    _write(artifact_path,artifact)
    _write(artifact_path.with_name("understanding-delivery.json"),
           {"delivered":True,"sha256":edits.sha(artifact_path),"call_id":"understand"})
    monkeypatch.setattr(owner_preview.model_map,"load_map",lambda *args: {})
    monkeypatch.setattr(owner_preview.model_map,"validate_understanding",lambda *args:
                        {"snapshot_path":str(snapshot),"operations":base})
    monkeypatch.setattr(owner_preview.owner_markings,"seal",lambda source,operations:operations)
    return SimpleNamespace(first=first, edit_root=f.destination, preview=preview, path=path,
                           artifact=artifact, image=image, source=f.source)


def test_selected_amount_is_exact_immutable_authority_and_source_stays_unchanged(shown_choices):
    f = shown_choices
    original, first = edits.tree_hashes(f.source), edits.tree_hashes(f.first)
    second = edits.create_edit(f.edit_root, "I choose A.\r\n", option_id="A")
    doc = edits.load(second)
    chosen = f.preview["options"][0]["operations"]
    assert doc["confirmation_text"] == "I choose A.\r\n"
    assert doc["understanding"] == f.artifact
    assert doc["selected_operations"] == chosen
    assert edits.Authority(second).resolve()["all_operations"] == chosen
    assert "replace the base proposed amounts" in edits.brief_suffix(doc)
    assert '"delta_mm": [0.1, 0.0, 0.0]' in edits.brief_suffix(doc)
    assert '"vertex_weights"' not in edits.brief_suffix(doc)
    assert chosen[0]["scope"]["vertex_weights"]  # full authority is preserved separately
    assert edits.tree_hashes(f.source) == original and edits.tree_hashes(f.first) == first
    assert not (second/"runs").exists() and not (second/"budget").exists()
    copied = second / f.preview["preview_receipt"]
    altered = edits.read(copied)
    altered["options"][0]["operations"][0]["params"]["delta_mm"][0] = 99
    _write(copied, altered)
    with pytest.raises(ValueError, match="input changed"):
        edits.Authority(second).resolve()


@pytest.mark.parametrize("damage", ["missing", "unknown", "receipt", "image", "undelivered", "unseen", "head"])
def test_invalid_or_unseen_choice_refuses_before_creating_round(shown_choices, damage):
    f = shown_choices
    option = None if damage == "missing" else "unknown" if damage == "unknown" else "A"
    if damage == "receipt":
        f.path.write_bytes(f.path.read_bytes()+b" ")
    if damage == "image":
        f.image.write_bytes(b"unshown replacement")
    if damage == "undelivered":
        delivery = edits.read(f.path.with_name("preview-delivery.json"))
        delivery["delivered"] = False
        _write(f.path.with_name("preview-delivery.json"),delivery)
    if damage == "unseen":
        _write(f.first / "work/job_scene/state.json", {"unseen":["preview"]})
    if damage == "head":
        index = CheckpointIndex(f.first / "checkpoints")
        index.append({**index.head,"id":"c0001"})
    with pytest.raises(ValueError):
        edits.create_edit(f.edit_root,"Choose it.",option_id=option)
    assert not (f.edit_root / "round-002-edit").exists()


def test_cli_preserves_option_and_confirmation_without_starting_any_provider():
    args = cli.parser().parse_args(["job","edit","example","--edit-id","edit-example",
        "--confirm","Choose B.","--option","B","--cap","23","--settled-stop","20"])
    assert args.option == "B" and args.confirm == "Choose B."


def test_selected_preview_requires_the_whole_ordered_group(shown_choices):
    f = shown_choices
    second = edits.create_edit(f.edit_root, "A.", option_id="A")
    with pytest.raises(ValueError, match="complete ordered"):
        edits.Authority(second).resolve([])
    from glasses3d.agent import owner_edits
    sealed = edits.Authority(second).resolve()
    sealed["correction"] = True
    with pytest.raises(ValueError, match="cannot be replaced"):
        owner_edits.corrected_operations(sealed,[{"operation_id":"move","params":{"delta_mm":[.5,0,0]}}])


@pytest.mark.parametrize("variant", ["subset", "reversed", "complete"])
def test_manual_edit_must_equal_selected_preview_not_a_different_operation_order(variant):
    from glasses3d.agent import owner_edits
    from glasses3d.tests.test_owner_mesh import box, operation, snapshot
    obj = box()
    source = snapshot(obj)
    move = operation(obj, [.2,0,0])
    rotate = copy.deepcopy(move)
    # Noncommuting, physically valid amounts: scaling followed by a widening.
    # Rotating only one wall would create a zero-thickness rim and must refuse.
    rotate.update(id="scale", kind="scale_region",
                  params={'strength':1.,'factors':[1.1,1.,1.],'anchor_mm':[0.,0.,0.]})
    operations = [rotate,move]
    sealed = {"snapshot":source,"source_fingerprint":"source", "all_operations":operations,
              "selected_preview":{"id":"A"}, "receipts":[]}
    actual = [rotate] if variant == "subset" else operations[::-1] if variant == "reversed" else operations
    current = owner_edits._advance(source,actual)
    if variant == "complete":
        assert owner_edits.confirmed_transition(sealed,current,"source") == operations
    else:
        with pytest.raises(ValueError, match="confirmed surface operation"):
            owner_edits.confirmed_transition(sealed,current,"source")


def test_preview_guard_rejects_changed_shading_even_when_positions_match(tmp_path, monkeypatch):
    from glasses3d.agent import owner_edits
    from glasses3d.tests.test_owner_mesh import box, operation, snapshot
    obj = box()
    source, operations = snapshot(obj), [operation(obj)]
    current = owner_edits._advance(source,operations)
    current["fingerprint"] = "same-positions-different-normals"
    sealed = {"snapshot":source,"all_operations":operations,"source_fingerprint":"source",
              "selected_preview":{"id":"A","fingerprint":"exact-preview"}}
    authority = SimpleNamespace(resolve=lambda *args:sealed)
    scene = SimpleNamespace(s=SimpleNamespace(head={"fingerprint":current["fingerprint"]}))
    monkeypatch.setattr(owner_edits,"snapshot",lambda *args:(current,tmp_path/"snapshot.json"))
    monkeypatch.setattr(owner_edits,"verify_snapshots",lambda *args:{"ok":True})
    result = owner_edits.ScopeGuard(authority).verify(scene)
    assert result["ok"] is False and result["code"] == "E_OWNER_AMOUNT"
    assert "including normals" in result["message"]
