"""Immutable asset verdicts, exact notes and non-destructive legacy migration; no worker or network."""
from concurrent.futures import ThreadPoolExecutor
import copy
import hashlib
import json
from pathlib import Path

import pytest

from glasses3d.agent import verdicts as V
from glasses3d.agent.session_inputs import plan_session_input


@pytest.fixture
def deliveries(tmp_path, monkeypatch):
    rows = {"delivery-old": {"id": "delivery-old", "glb_sha256": "a" * 64, "manifest_sha256": "b" * 64},
            "delivery-new": {"id": "delivery-new", "glb_sha256": "c" * 64, "manifest_sha256": "d" * 64}}
    state = {"current": "delivery-old"}

    def load(root, ident=None):
        assert Path(root) == tmp_path
        try:
            return copy.deepcopy(rows[ident or state["current"]])
        except KeyError:
            raise ValueError("missing delivery") from None

    monkeypatch.setattr(V, "_delivery", load)
    return rows, state


def test_verdict_is_explicit_asset_bound_and_append_only(tmp_path, deliveries):
    rows, state = deliveries
    with pytest.raises(V.VerdictError, match="explicit delivery"):
        V.append_verdict(tmp_path, "accept", "looks right")
    first = V.append_verdict(tmp_path, "changes", "  Slim the bridge.\nKeep the colour — please.  ", expected_sha256="a" * 64)
    path = V.ledger_file(tmp_path)
    prefix = path.read_bytes()
    assert first["schema"] == "g3d.verdict/1" and first["delivery_id"] == "delivery-old"
    assert first["glb_sha256"] == "a" * 64 and first["manifest_sha256"] == "b" * 64
    state["current"] = "delivery-new"
    with pytest.raises(V.VerdictError, match="reviewed GLB"):
        V.append_verdict(tmp_path, "accept", "old model", expected_sha256="a" * 64)
    second = V.append_verdict(tmp_path, "accept", "Old version is accepted", delivery_id="delivery-old")
    assert second["delivery_id"] == "delivery-old" and second["previous_sha256"] == first["record_sha256"]
    assert path.read_bytes().startswith(prefix)
    assert V.read_verdicts(tmp_path) == [first, second]
    note = V.pending_notes(tmp_path)[0]
    assert first["id"] in note and first["glb_sha256"] in note and note.endswith(first["notes"])
    assert len(V.pending_notes(tmp_path)) == 1


@pytest.mark.parametrize("verdict,notes", [("prefer_B", "x"), ("changes", "  \n"), ("accept", None), ([], "x")])
def test_invalid_verdicts_never_write_a_record(tmp_path, deliveries, verdict, notes):
    with pytest.raises(V.VerdictError):
        V.append_verdict(tmp_path, verdict, notes, delivery_id="delivery-old")
    assert not V.ledger_file(tmp_path).exists()


def test_expected_sha_must_match_even_with_explicit_delivery(tmp_path, deliveries):
    with pytest.raises(V.VerdictError, match="reviewed GLB"):
        V.append_verdict(tmp_path, "accept", "", delivery_id="delivery-old", expected_sha256="c" * 64)
    with pytest.raises(V.VerdictError, match="lowercase sha256"):
        V.append_verdict(tmp_path, "accept", "", expected_sha256="A" * 64)
    assert not V.ledger_file(tmp_path).exists()


@pytest.mark.parametrize("damage", ["edit", "truncate_tail", "missing_head", "missing_ledger", "partial_line", "duplicate_key"])
def test_tampered_ledger_refuses_read_and_further_append(tmp_path, deliveries, damage):
    V.append_verdict(tmp_path, "changes", "first", delivery_id="delivery-old")
    V.append_verdict(tmp_path, "accept", "second", delivery_id="delivery-old")
    path, head = V.ledger_file(tmp_path), V._head_path(V.ledger_file(tmp_path))
    raw = path.read_bytes()
    if damage == "edit":
        path.write_bytes(raw.replace(b'"notes":"first"', b'"notes":"tampered"'))
    elif damage == "truncate_tail":
        path.write_bytes(raw.splitlines(keepends=True)[0])
    elif damage == "missing_head":
        head.unlink()
    elif damage == "missing_ledger":
        path.unlink()
    elif damage == "partial_line":
        path.write_bytes(raw + b'{"schema":')
    else:
        path.write_bytes(raw.replace(b'"notes":"first"', b'"notes":"first","notes":"other"'))
    damaged = path.read_bytes() if path.exists() else None
    with pytest.raises(V.VerdictError):
        V.pending_notes(tmp_path)
    with pytest.raises(V.VerdictError):
        V.append_verdict(tmp_path, "accept", "never", delivery_id="delivery-old")
    assert (path.read_bytes() if path.exists() else None) == damaged


def test_changed_delivery_manifest_is_refused_on_read_and_append(tmp_path, deliveries):
    rows, _state = deliveries
    V.append_verdict(tmp_path, "changes", "bridge", delivery_id="delivery-old")
    rows["delivery-old"]["manifest_sha256"] = "e" * 64
    with pytest.raises(V.VerdictError, match="binding changed"):
        V.read_verdicts(tmp_path)
    with pytest.raises(V.VerdictError, match="binding changed"):
        V.append_verdict(tmp_path, "accept", "", delivery_id="delivery-new")


def test_interrupted_head_write_does_not_silently_repair_or_drop_verdict(tmp_path, deliveries, monkeypatch):
    V.append_verdict(tmp_path, "changes", "first", delivery_id="delivery-old")
    prefix = V.ledger_file(tmp_path).read_bytes()
    monkeypatch.setattr(V, "write_json_atomic", lambda *a, **kw: (_ for _ in ()).throw(OSError("disk write failed")))
    with pytest.raises(OSError, match="disk write"):
        V.append_verdict(tmp_path, "changes", "second", delivery_id="delivery-old")
    assert V.ledger_file(tmp_path).read_bytes().startswith(prefix)
    with pytest.raises(V.VerdictError, match="head receipt"):
        V.read_verdicts(tmp_path)


def test_concurrent_append_has_one_chain_and_no_lost_notes(tmp_path, deliveries):
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(lambda n: V.append_verdict(tmp_path, "changes", f"note {n}", delivery_id="delivery-old"), range(12)))
    saved = V.read_verdicts(tmp_path)
    assert len(saved) == len({row["id"] for row in rows}) == 12
    assert {row["notes"] for row in saved} == {f"note {n}" for n in range(12)}
    assert [row["sequence"] for row in saved] == list(range(1, 13))


def test_note_candidates_use_actual_session_history_not_sent_flags(tmp_path, deliveries):
    first = V.append_verdict(tmp_path, "changes", "  Make it narrower\nKeep blue.  ", delivery_id="delivery-old")
    notes = V.pending_notes(tmp_path)
    planned = plan_session_input([], prompt="job", photos=[], invocation_text="run one", owner_notes=notes)
    assert planned.receipt["notes_sent"] == 1
    persisted = [{"role": "user", "content": planned.content}]
    again = plan_session_input(persisted, prompt="job", photos=[], invocation_text="run two", owner_notes=V.pending_notes(tmp_path))
    assert again.receipt["notes_sent"] == 0
    # The owner can repeat the same words in a distinct decision; its identity must not suppress that new request.
    second = V.append_verdict(tmp_path, "changes", first["notes"], delivery_id="delivery-old")
    assert first["id"] != second["id"]
    next_plan = plan_session_input(persisted, prompt="job", photos=[], invocation_text="run three", owner_notes=V.pending_notes(tmp_path))
    assert next_plan.receipt["notes_sent"] == 1
    assert next_plan.receipt["owner_notes"][0]["action"] == "reuse_history"
    assert any(part.get("text", "").endswith(first["notes"]) for part in next_plan.content)


def test_missing_ledger_returns_no_candidates(tmp_path, deliveries):
    assert V.read_verdicts(tmp_path) == V.pending_notes(tmp_path) == []


def legacy_source(tmp_path):
    path = tmp_path / "owner_verdicts.jsonl"
    rows = [{"date": "2026-10-06", "product": f"Frame {n}", "glb_sha256": f"{n:064x}",
             "verdict": "accept", "owner_words": "all three can be considered perfect.", "source": "historical review"}
            for n in range(1, 4)]
    for n, verdict in enumerate(("prefer_option_B", "owner_selected_level_promoted",
                                  "appearance_close_enough_continue_library_proof",
                                  "observed_slope_plus_poisson_selected_and_wp5_complete_by_owner"), 4):
        rows.append({"date": "2026-10-07", "product": "WP5 TOOL TEST", "glb_sha256": f"{n:064x}",
                     "print_png_sha256": f"{n + 10:064x}", "verdict": verdict,
                     "owner_words": "  Keep this choice — please.\nContinue the proof.  ",
                     "new_owner_review": n != 5, "source": "tool test, not a product delivery"})
    path.write_bytes(b"".join(json.dumps(row, ensure_ascii=False).encode("utf-8") + b"\r\n" for row in rows))
    return path


def test_legacy_migration_preserves_seven_records_and_only_three_whole_asset_accepts(tmp_path):
    source = legacy_source(tmp_path)
    original = source.read_bytes()
    target = tmp_path / "normalized/verdicts.jsonl"
    report = V.migrate_legacy(source, target)
    assert report["source_records"] == 7 and report["product_accepts"] == 3 and report["archived_scoped_decisions"] == 4
    rows = V.read_verdicts(tmp_path, ledger_path=target)
    assert len(rows) == 3 and all(row["verdict"] == "accept" and row["origin"] == "legacy" for row in rows)
    archive = json.loads(Path(report["legacy_records"]).read_text(encoding="utf-8"))
    lines = original.splitlines(keepends=True)
    for n, rec in enumerate(archive["records"]):
        assert rec["raw_line"].encode("utf-8") == lines[n]
        assert rec["line_sha256"] == hashlib.sha256(lines[n]).hexdigest()
        assert rec["record"] == json.loads(lines[n])
    assert all(rec["scope"] == "legacy_scoped_decision" for rec in archive["records"][3:])
    assert [row["notes"] for row in rows] == [json.loads(line)["owner_words"] for line in lines[:3]]
    paths = [source, target, V._head_path(target), Path(report["legacy_records"]), Path(report["report"])]
    before = {path: path.read_bytes() for path in paths}
    assert V.migrate_legacy(source, target) == report
    assert {path: path.read_bytes() for path in paths} == before


@pytest.mark.parametrize("damaged", ["source", "archive", "report", "normalized"])
def test_migration_tamper_refuses_without_rewriting_evidence(tmp_path, damaged):
    source = legacy_source(tmp_path)
    target = tmp_path / "normalized/verdicts.jsonl"
    report = V.migrate_legacy(source, target)
    path = {"source": source, "archive": Path(report["legacy_records"]), "report": Path(report["report"]), "normalized": target}[damaged]
    path.write_bytes(path.read_bytes().replace(b"perfect", b"changed") if damaged in {"source", "archive", "normalized"}
                     else b'{"schema":"bad"}')
    originals = {p: p.read_bytes() for p in (source, target, Path(report["legacy_records"]), Path(report["report"]))}
    with pytest.raises(V.VerdictError):
        V.migrate_legacy(source, target)
    assert {p: p.read_bytes() for p in originals} == originals


def test_malformed_legacy_creates_no_outputs(tmp_path):
    source = legacy_source(tmp_path)
    source.write_bytes(source.read_bytes() + b'{"verdict":\n')
    target = tmp_path / "normalized/verdicts.jsonl"
    with pytest.raises(V.VerdictError):
        V.migrate_legacy(source, target)
    assert not target.parent.exists()


@pytest.mark.parametrize("scope", ["print", "method"])
def test_scoped_accept_does_not_become_a_product_accept(tmp_path, scope):
    source = tmp_path / "old.jsonl"
    source.write_text(json.dumps({"verdict": "accept", "owner_words": "good", "product": "Frame", "scope": scope,
                                  "date": "2026-10-07", "glb_sha256": "a" * 64}) + "\n")
    report = V.migrate_legacy(source, tmp_path / "normalized/verdicts.jsonl")
    assert report["product_accepts"] == 0 and report["archived_scoped_decisions"] == 1
    assert V.read_verdicts(tmp_path, ledger_path=report["ledger"]) == []


def test_real_immutable_delivery_binding_survives_new_current_and_refuses_modified_asset(tmp_path):
    from glasses3d.agent import delivery as D
    from glasses3d.tests.agent.test_delivery import make_finished
    first_scene, first_result = make_finished(tmp_path, run_id="first")
    first = D.deliver_finished(tmp_path, first_scene, first_result)
    verdict = V.append_verdict(tmp_path, "changes", "Slim the bridge.", delivery_id=first["id"])
    second_scene, second_result = make_finished(tmp_path, run_id="second", variation=1)
    second = D.deliver_finished(tmp_path, second_scene, second_result)
    assert first["glb_sha256"] != second["glb_sha256"]
    assert V.read_verdicts(tmp_path) == [verdict] and first["glb_sha256"] in V.pending_notes(tmp_path)[0]
    with pytest.raises(V.VerdictError, match="reviewed GLB"):
        V.append_verdict(tmp_path, "accept", "", expected_sha256=first["glb_sha256"])
    V.append_verdict(tmp_path, "accept", "Reviewed the first.", delivery_id=first["id"])
    Path(D.load_delivery(tmp_path, first["id"])["model"]).write_bytes(b"tampered asset")
    with pytest.raises(ValueError):
        V.pending_notes(tmp_path)
