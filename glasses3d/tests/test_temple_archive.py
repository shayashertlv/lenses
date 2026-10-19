"""Historical builders remain isolated, authenticated and available in edit copies."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile

import pytest

from glasses3d.g3d import temple_archive as archive
from glasses3d.agent import edits
from glasses3d.agent.temple_lineage import carry_evidence


def digest(value):
    return hashlib.sha256(value).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def make_run(root, run_id="old", *, created="2026-01-01T00:00:00+00:00", marker="historical",
             extras=(), source=None, build_source=None):
    run = root / "runs" / run_id
    run.mkdir(parents=True)
    sources = source or {archive.SOURCE_PREFIX + name + ".py": b"" for name in archive.MODULES}
    if source is None:
        sources[archive.SOURCE_PREFIX + "spec.py"] = (
            "from dataclasses import dataclass\n@dataclass\nclass Result:\n    value: str\n").encode()
        sources[archive.SOURCE_PREFIX + "build.py"] = (build_source or
            f"from .spec import Result\ndef build_temple(value):\n    return Result({marker!r})\n").encode()
    with tarfile.open(run / "src.tar.gz", "w:gz") as tar:
        for name, payload in sources.items():
            member = tarfile.TarInfo(name)
            member.size = len(payload)
            tar.addfile(member, io.BytesIO(payload))
        for member, payload in extras:
            tar.addfile(member, io.BytesIO(payload) if payload is not None else None)
    write_json(run / "implementation.json", {"schema": "g3d.implementation/1", "archive": "src.tar.gz",
        "archive_sha256": digest((run / "src.tar.gz").read_bytes()),
        "files": {name: digest(payload) for name, payload in sources.items()}, "git_head": "example"})
    write_json(run / "run-config.json", {"job_id": root.name, "run_id": run_id, "created": created,
        "implementation": {"path": "implementation.json", "sha256": digest((run / "implementation.json").read_bytes()),
                           "archive_sha256": digest((run / "src.tar.gz").read_bytes())}})
    return run


def rebuild_bindings(run):
    receipt = json.loads((run / "implementation.json").read_text())
    receipt["archive_sha256"] = digest((run / "src.tar.gz").read_bytes())
    write_json(run / "implementation.json", receipt)
    config = json.loads((run / "run-config.json").read_text())
    config["implementation"].update(sha256=digest((run / "implementation.json").read_bytes()),
                                     archive_sha256=receipt["archive_sha256"])
    write_json(run / "run-config.json", config)


def test_archived_builders_do_not_replace_current_modules_or_import_parent_init(tmp_path):
    from glasses3d.g3d.temple import build as current_build
    before = current_build.build_temple
    member = tarfile.TarInfo(archive.SOURCE_PREFIX + "__init__.py")
    payload = b"raise AssertionError('archived package initializer must not execute')\n"
    member.size = len(payload)
    make_run(tmp_path, extras=[(member, payload)])
    errors = []
    [(builder, provenance)] = list(archive.archived_builders(tmp_path, errors=errors))
    assert not errors
    assert builder({}).value == "historical"
    assert builder.__module__.startswith("_g3d_temple_archive_")
    assert current_build.build_temple is before
    assert sys.modules[current_build.__name__] is current_build
    assert provenance["run_id"] == "old" and len(provenance["source_sha256"]) == 4


@pytest.mark.parametrize("damage", ["archive", "receipt", "source_hash", "binding", "missing_archive", "missing_config"])
def test_any_broken_hash_chain_refuses_without_current_builder_fallback(tmp_path, damage):
    run = make_run(tmp_path)
    if damage == "archive":
        (run / "src.tar.gz").write_bytes((run / "src.tar.gz").read_bytes() + b"changed")
    elif damage == "receipt":
        (run / "implementation.json").write_bytes((run / "implementation.json").read_bytes() + b" ")
    elif damage == "source_hash":
        receipt = json.loads((run / "implementation.json").read_text())
        receipt["files"][archive.SOURCE_PREFIX + "build.py"] = "0" * 64
        write_json(run / "implementation.json", receipt)
        rebuild_bindings(run)
    elif damage == "binding":
        config = json.loads((run / "run-config.json").read_text())
        config["implementation"]["path"] = "../implementation.json"
        write_json(run / "run-config.json", config)
    elif damage == "missing_archive":
        (run / "src.tar.gz").unlink()
    else:
        (run / "run-config.json").unlink()
    errors = []
    assert list(archive.archived_builders(tmp_path, errors=errors)) == []
    assert errors


@pytest.mark.parametrize("name,kind", [
    ("../escape.py", "regular"), ("/absolute.py", "regular"), ("C:/escape.py", "regular"),
    ("glasses3d\\escape.py", "regular"), ("glasses3d/.env", "regular"),
    ("glasses3d/.env.local", "regular"), (archive.SOURCE_PREFIX + "build.py", "regular"),
    ("other-link", "symlink"), ("other-link", "hardlink"), ("directory", "directory")])
def test_unsafe_archive_members_refuse_before_any_source_exec(tmp_path, name, kind):
    member = tarfile.TarInfo(name)
    member.type = {"regular": tarfile.REGTYPE, "symlink": tarfile.SYMTYPE,
                   "hardlink": tarfile.LNKTYPE, "directory": tarfile.DIRTYPE}[kind]
    member.linkname = "elsewhere"
    member.size = 0
    make_run(tmp_path, extras=[(member, b"" if kind == "regular" else None)])
    errors = []
    assert list(archive.archived_builders(tmp_path, errors=errors)) == []
    assert errors
    assert not (tmp_path.parent / "escape.py").exists()


@pytest.mark.parametrize("source", ["import bpy\n", "import os\n", "open('should-never-exist', 'w')\n"])
def test_archived_sources_cannot_load_blender_or_open_files(tmp_path, source):
    make_run(tmp_path, build_source=source)
    errors = []
    assert list(archive.archived_builders(tmp_path, errors=errors)) == []
    assert errors and "unavailable" in errors[0]


def test_no_global_search_for_missing_source_and_identical_sources_deduplicate(tmp_path):
    make_run(tmp_path / "unrelated")
    assert list(archive.archived_builders(tmp_path / "chosen")) == []
    make_run(tmp_path / "chosen", "first")
    make_run(tmp_path / "chosen", "second")
    assert len(list(archive.archived_builders(tmp_path / "chosen"))) == 1


def test_edit_lineage_copy_is_independent_temporally_scoped_and_hash_bound(tmp_path):
    source, target, later = (tmp_path / name for name in ("source", "copy", "next-copy"))
    first = make_run(source, "first")
    selected = make_run(source, "selected", marker="selected", created="2026-02-01T00:00:00+00:00")
    make_run(source, "future", marker="future", created="2026-03-01T00:00:00+00:00")
    hashes = carry_evidence(source, target, run_id="selected")
    write_json(target / "inputs/owner-edit.json", {"immutable_files": hashes})
    assert len(hashes) == 7
    assert {build({}).value for build, _ in archive.archived_builders(target)} == {"historical", "selected"}
    # A later edit of the owner-applied delivery uses the preserved inputs, even
    # when the original archive is unavailable and no provider run exists.
    (first / "src.tar.gz").unlink()
    (selected / "src.tar.gz").unlink()
    later_hashes = carry_evidence(target, later, run_id="owner-apply")
    write_json(later / "inputs/owner-edit.json", {"immutable_files": later_hashes})
    assert {build({}).value for build, _ in archive.archived_builders(later)} == {"historical", "selected"}
    index_path = later / "inputs/temple-lineage/index.json"
    index_path.write_bytes(index_path.read_bytes() + b" ")
    errors = []
    assert list(archive.archived_builders(later, errors=errors)) == []
    assert "immutable" in errors[0]


@pytest.mark.parametrize("fixture", ["slab", "pyramid", "valley", "crease_rise"])
def test_real_temple_sources_build_under_isolated_namespace(tmp_path, fixture):
    from glasses3d.g3d.temple import fixtures
    from glasses3d.g3d.temple.build import build_temple
    location = Path(archive.__file__).parent / "temple"
    sources = {archive.SOURCE_PREFIX + name + ".py": (location / (name + ".py")).read_bytes()
               for name in archive.MODULES}
    make_run(tmp_path, source=sources)
    [(old_build, _)] = list(archive.archived_builders(tmp_path))
    spec = getattr(fixtures, fixture)()
    expected, actual = build_temple(spec), old_build(spec)
    assert actual.spec.sha256 == expected.spec.sha256
    assert actual.polys == expected.polys
    assert (actual.V == expected.V).all()


def test_external_runtime_preserves_valid_lineage_already_copied_from_source(tmp_path):
    source, target = tmp_path / "source", tmp_path / "copy"
    make_run(source)
    hashes = carry_evidence(source, target, run_id="old")
    write_json(target / "inputs/owner-edit.json", {"immutable_files": hashes})
    # A checkpoint-only runtime is separate from already copied source inputs.
    hashes = carry_evidence(tmp_path / "checkpoint-runtime", target, run_id="owner-apply")
    write_json(target / "inputs/owner-edit.json", {"immutable_files": hashes})
    assert len(hashes) == 4
    assert [build({}).value for build, _ in archive.archived_builders(target)] == ["historical"]


@pytest.mark.parametrize("damage", ["files", "row", "directory", "immutable", "unavailable"])
def test_malformed_carried_lineage_refuses_without_aborting_copy_or_loading_code(tmp_path, damage):
    source, target = tmp_path / "source", tmp_path / "copy"
    make_run(source)
    hashes = carry_evidence(source, target, run_id="old")
    index_path = target / "inputs/temple-lineage/index.json"
    index = json.loads(index_path.read_text())
    if damage == "files":
        index["archives"][0]["files"] = []
    elif damage == "row":
        index["archives"][0] = []
    elif damage == "directory":
        index["archives"][0]["directory"] = "../outside"
    elif damage == "unavailable":
        index["unavailable"] = "not a list"
    write_json(index_path, index)
    hashes["inputs/temple-lineage/index.json"] = digest(index_path.read_bytes())
    write_json(target / "inputs/owner-edit.json", {"immutable_files": [] if damage == "immutable" else hashes})
    errors = []
    assert list(archive.archived_builders(target, errors=errors)) == []
    assert errors and "Copied temple lineage unavailable" in errors[0]
    # The refusal is carried as evidence; unrelated owner edits can still copy.
    assert carry_evidence(target, tmp_path / "next-copy", run_id="owner-apply")


@pytest.mark.parametrize("separate_runtime", [False, True])
def test_create_understand_carries_every_source_file_and_next_round_inputs(tmp_path, monkeypatch, separate_runtime):
    from types import SimpleNamespace
    from glasses3d.agent.journal import CheckpointIndex
    source = tmp_path / "source"
    make_run(source, "selected")
    write_json(source / "job.json", {"placeholder": True})
    monkeypatch.setattr(edits.jobs, "load_job", lambda root: {})
    monkeypatch.setattr("glasses3d.agent.print_freshness.carry_evidence", lambda *args: None)
    index = CheckpointIndex(source / "checkpoints")
    index.dir.mkdir(parents=True)
    payload = b"selected checkpoint"
    (index.dir / "c0001.blend").write_bytes(payload)
    row = {"id": "c0001", "kind": "save", "file": "c0001.blend", "sha256": digest(payload),
           "fingerprint": "a" * 64}
    index.append(row)
    basis = source / "cameras/frozen"
    write_json(basis / "basis.json", {})
    monkeypatch.setattr(edits, "_source_basis", lambda *args, **kwargs: SimpleNamespace(id="frozen", dir=basis))
    monkeypatch.setattr(edits.delivery, "load_delivery", lambda *args: {"delivery_id": "chosen", "glb_sha256": "b" * 64,
        "report_data": {"checkpoint": "c0001", "fingerprint": row["fingerprint"], "run_id": "selected"}})
    before = edits.tree_hashes(source)
    extra = {}
    if separate_runtime:
        import shutil
        runtime = tmp_path / "checkpoint-only-runtime"
        shutil.copytree(source / "checkpoints", runtime / "checkpoints")
        extra["source_runtime"] = runtime
    copied = edits.create_understand(source, "Move this corner", destination=tmp_path / "edit", **extra)
    doc = edits.load(copied)
    lineage = {name: value for name, value in doc["immutable_files"].items() if "temple-lineage" in name}
    assert len(lineage) == 4
    assert edits.tree_hashes(source) == before
    next_copy = tmp_path / "next-round"
    edits._copy_inputs(copied, next_copy)
    assert {build({}).value for build, _ in archive.archived_builders(next_copy)} == {"historical"}
    chosen_file = next(name for name in lineage if name.endswith("src.tar.gz"))
    (next_copy / chosen_file).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="input changed"):
        edits.load(next_copy)
