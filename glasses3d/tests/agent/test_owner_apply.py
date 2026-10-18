"""Host application refuses drift and uses no paid runner for a sealed selection."""
from __future__ import annotations

from types import SimpleNamespace

from PIL import Image
import pytest

from glasses3d.agent import cli, edits, job_run, owner_apply
from .test_owner_edit_lifecycle_review import selected_source, _write
from .test_owner_understanding_dispositions import shown, finish, item


@pytest.fixture
def local_apply(tmp_path, monkeypatch):
    root = tmp_path / "applied"
    image = root / "work/pv-assets/native.png"
    image.parent.mkdir(parents=True)
    Image.new("RGB", (90, 20), "white").save(image)
    source = "a" * 64
    target = "b" * 64
    ops = [{"id": "near-left", "params": {"delta_mm": [3, 0, 0]}},
           {"id": "near-right", "params": {"delta_mm": [-3, 0, 0]}}]
    selected = {"id": "B", "label": "3 mm toward head", "scale": 3., "operations": ops,
                "fingerprint": target}
    preview_path = "work/pv/example/preview.json"
    exported = {"status": "exported", "fingerprint": target, "sha256": "c" * 64,
                "validate_spec": {"ok": True}, "print_check": {"ok": True},
                "path": str(root / "work/applied.glb")}
    _write(root / preview_path, {"options": [{**selected, "export": exported,
        "full_pairs": [{"path": "C:/unavailable/original.png", "sha256": edits.sha(image),
                         "view": "top: photo / before / option B"}]}],
        "evidence_files": {image.relative_to(root).as_posix(): edits.sha(image)}})
    doc = {"selected_preview": selected, "selected_preview_option": "B", "selected_operations": ops,
           "request_text": "Both temples toward the head.",
           "source": {"fingerprint": source, "basis": "basis-001"},
           "understanding": {"amount_preview": {"preview_receipt": preview_path}}}
    calls = []
    session = SimpleNamespace(head={"id": "c0000", "fingerprint": source})
    session.fingerprint = lambda: session.head["fingerprint"]
    scene = SimpleNamespace(s=session, owner_edit_authority=object(),
        seeing=SimpleNamespace(export_head=lambda cid: dict(exported)),
        exit_checkpoint=lambda: {"verified": True},
        start=lambda: calls.append("start"), close=lambda: calls.append("close"))
    _write(root / "cameras/basis-001/basis.json", {"id": "basis-001"})

    def apply(scene, ids, call_id):
        calls.append((ids, call_id))
        session.head = {"id": "c0001", "fingerprint": target}
        return {"status": "ok", "operation_ids": ids, "fingerprint": target}

    monkeypatch.setattr(edits, "create_edit", lambda *args, **kwargs: root)
    monkeypatch.setattr(edits, "load", lambda path: doc)
    monkeypatch.setattr(owner_apply.edit_capabilities, "require_round", lambda *args: None)
    monkeypatch.setattr(owner_apply, "JobScene", lambda path: scene)
    monkeypatch.setattr(edits, "record_round", lambda *args: None)
    from glasses3d.agent.tools import arview
    monkeypatch.setattr(arview, "capture", lambda *args: {"status": "ok"})
    monkeypatch.setattr(owner_apply.delivery, "deliver_owner_applied", lambda *args: {"status": "delivered"})
    monkeypatch.setattr(owner_apply.owner_edits, "apply", apply)
    monkeypatch.setattr(owner_apply.owner_edits, "ScopeGuard",
                        lambda authority: SimpleNamespace(verify=lambda scene: {"ok": True}))
    def paid(*args, **kwargs):
        raise AssertionError("offline apply reached a paid runner")
    monkeypatch.setattr(job_run, "prepare_run", paid)
    monkeypatch.setattr(job_run, "execute_run", paid)
    return SimpleNamespace(root=root, doc=doc, scene=scene, calls=calls, exported=exported, image=image)


def test_apply_exact_order_with_native_sealed_evidence_and_no_paid_round(local_apply):
    h = local_apply
    result = owner_apply.apply_preview(h.root.parent, "Demonstrate B on a tool-test copy.", "B", tool_test=True)
    assert result["status"] == "applied" and result["api_requests"] == 0
    assert result["settled_usd"] == "0.000000" and result["visual_acceptance"] is False
    assert result["tool_test"] and result["exact_preview_fingerprint"]
    assert result["export_matches_preview_bytes"]
    assert result["delivery"]["status"] == "delivered"
    assert h.calls == ["start", (["near-left", "near-right"], "host-preview-apply"), "close"]
    assert result["images"][0]["path"] == str(h.image)
    assert "sealed selected preview" in (h.root / "work/owner_edit/applied-preview.html").read_text()
    assert not (h.root / "runs").exists() and not (h.root / "budget").exists()


@pytest.mark.parametrize("failure", ["source", "refused", "fingerprint", "spec", "export_fingerprint", "scope"])
def test_apply_refuses_inexact_or_invalid_result_and_closes_worker(local_apply, monkeypatch, failure):
    h = local_apply
    if failure == "source":
        h.scene.s.head["fingerprint"] = "d" * 64
    elif failure in ("refused", "fingerprint"):
        monkeypatch.setattr(owner_apply.owner_edits, "apply", lambda *args: {
            "status": "refused" if failure == "refused" else "ok"})
    elif failure == "spec":
        h.exported["validate_spec"]["ok"] = False
    elif failure == "export_fingerprint":
        h.exported["fingerprint"] = "d" * 64
    else:
        monkeypatch.setattr(owner_apply.owner_edits, "ScopeGuard",
            lambda authority: SimpleNamespace(verify=lambda scene: {"ok": False}))
    with pytest.raises(ValueError):
        owner_apply.apply_preview(h.root.parent, "Apply B.", "B")
    assert h.calls[-1] == "close"
    assert not (h.root / "work/owner_edit/applied-preview.json").exists()


def test_changed_native_evidence_refuses_before_starting_worker(local_apply):
    h = local_apply
    h.image.write_bytes(b"changed")
    with pytest.raises(ValueError, match="missing or changed"):
        owner_apply.apply_preview(h.root.parent, "Apply B.", "B")
    assert h.calls == []


def test_stale_diagnosis_refuses_before_starting_worker(local_apply, monkeypatch):
    h = local_apply
    def invalid(*args):
        raise ValueError("diagnosis is not of the selected source model")
    monkeypatch.setattr(owner_apply.edit_capabilities, "require_round", invalid)
    with pytest.raises(ValueError, match="diagnosis"):
        owner_apply.apply_preview(h.root.parent, "Apply B.", "B")
    assert h.calls == []


def test_changed_authority_during_apply_is_not_reported_success(local_apply, monkeypatch):
    h = local_apply
    count = 0
    def load(root):
        nonlocal count
        count += 1
        return h.doc if count == 1 else {**h.doc, "request_text": "unconfirmed replacement"}
    monkeypatch.setattr(edits, "load", load)
    with pytest.raises(ValueError, match="authority changed"):
        owner_apply.apply_preview(h.root.parent, "Apply B.", "B")
    assert h.calls[-1] == "close"
    assert not (h.root / "work/owner_edit/applied-preview.json").exists()


def test_cli_offline_apply_routes_without_estimate_or_money_flags(local_apply, monkeypatch):
    h = local_apply
    monkeypatch.setattr(cli, "resolve_job", lambda *args: h.root)
    monkeypatch.setattr(edits, "load", lambda path: {"phase": "understand"})
    captured = {}
    def apply(edit_root, words, option, **kwargs):
        captured.update(edit_root=edit_root, words=words, option=option, **kwargs)
        return {"status": "applied", "api_requests": 0}
    monkeypatch.setattr(owner_apply, "apply_preview", apply)
    args = cli.parser().parse_args(["job", "edit-apply", str(h.root), "--option", "B",
        "--confirm", "Demonstrate B.", "--output", str(h.root / "copy"), "--tool-test"])
    assert cli.dispatch(args)["api_requests"] == 0
    assert captured == {"edit_root": h.root.parent, "words": "Demonstrate B.", "option": "B",
                        "destination": h.root / "copy", "tool_test": True}


def test_explicit_copy_destination_keeps_original_understanding_index_unchanged(shown, tmp_path):
    h = shown
    finish(h, [item(h, disposition="executable")])
    before = edits.tree_hashes(h.root.parent)
    destination = tmp_path / "separate-copy"
    copied = edits.create_edit(h.root.parent, "Apply exactly.", option_id="full", destination=destination)
    assert copied == destination
    assert edits.tree_hashes(h.root.parent) == before
    assert edits.load(copied)["confirmation_text"] == "Apply exactly."
    with pytest.raises(ValueError, match="outside its unchanged"):
        edits.create_edit(h.root.parent, "Apply exactly.", option_id="full", destination=h.root / "nested-copy")
    with pytest.raises(FileExistsError):
        edits.create_edit(h.root.parent, "Apply exactly.", option_id="full", destination=destination)


def test_understanding_reports_local_zero_and_final_settled_count(shown):
    h = shown
    finish(h, [item(h)])
    direct = edits.read(h.root / "work/owner_edit/understanding-usage.json")
    assert direct["requests"] == 0 and direct["settled_usd"] == "0.000000"
    result = job_run._publish_result(h.root, "understand-run", h.scene, {
        "status": "completed", "requests": 3, "budget": {"settled_micro_usd": 123456}})
    assert result["usage"]["requests"] == 3 and result["usage"]["settled_usd"] == "0.123456"
    page = edits.understanding_page(h.root).read_text()
    assert "3 requests" in page and "$0.123456 settled" in page


def test_short_understanding_teaches_diagnosis_direction_and_free_host_application():
    from glasses3d.agent import instructions
    from glasses3d.agent.tools import build_tools
    owner = {"phase": "understand"}
    text = instructions.build_instructions(build_tools(SimpleNamespace(root=None, owner_edit=owner)), owner_edit=owner)
    assert "no whole-model review" in text
    assert "measure the visible cause" in text
    assert "never choose repair_shading merely because the request says texture" in text
    assert "Toward the head requires top and rear as well as side" in text
    assert "job edit-apply applies its exact operations at $0" in text
    assert "count and settled cost" in text
