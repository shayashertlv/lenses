"""Private amount previews keep producer evidence without inheriting edit authority."""
from types import SimpleNamespace

from glasses3d.agent import edits
from glasses3d.agent.temple_lineage import carry_evidence
from glasses3d.agent.tools import owner_preview
from glasses3d.g3d.temple_archive import archived_builders
from glasses3d.tests.test_temple_archive import make_run, write_json


def test_private_preview_carries_authenticated_lineage(tmp_path, monkeypatch):
    original, parent, child = (tmp_path / name for name in ("original", "understand", "preview"))
    make_run(original, "selected")
    hashes = carry_evidence(original, parent, run_id="selected")
    write_json(parent / "inputs/owner-edit.json", {"immutable_files": hashes})
    write_json(parent / "job.json", {"placeholder": True})
    write_json(parent / "conversation.json", {"must_not_be_copied": True})
    checkpoint = parent / "selected.blend"
    checkpoint.write_bytes(b"verified seed fixture")
    before = edits.tree_hashes(parent)
    monkeypatch.setattr(owner_preview.jobs, "load_job", lambda root: {})
    monkeypatch.setattr(owner_preview.print_freshness, "carry_evidence", lambda *args: None)
    seed = owner_preview._prepare(SimpleNamespace(root=parent), child, checkpoint,
                                  {"sha256": edits.sha(checkpoint)})
    assert seed.read_bytes() == checkpoint.read_bytes()
    assert {build({}).value for build, _ in archived_builders(child)} == {"historical"}
    assert not (child / "inputs/owner-edit.json").exists()
    assert not (child / "conversation.json").exists()
    assert edits.tree_hashes(parent) == before
