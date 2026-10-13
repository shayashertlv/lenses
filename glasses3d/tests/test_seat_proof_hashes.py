"""Seat proof staleness follows its implementation and fixtures, not unrelated shared-module edits."""
from glasses3d.tests.proofs import wp4_proof as proof


def test_seat_sources_and_runners_are_hashed():
    paths = set(proof.TOOL_SOURCES)
    assert {p.relative_to(proof.G3D).as_posix() for p in (proof.G3D / "g3d/seat").glob("*.py")} <= paths
    assert {"g3d/seat_bpy.py", "tests/blender/seat_run.py", "tests/blender/seat_build.py",
            "tests/fixtures/seat_fixtures.py", "tests/proofs/wp4_proof.py", "tests/proofs/wp4_proof_bpy.py"} <= paths
    assert paths.isdisjoint({"g3d/export_glb.py", "g3d/glb_patch.py", "g3d/__init__.py", "g3d_host/blender.py"})


def test_shared_edit_keeps_proof_current_but_seat_edit_stales_it(tmp_path, monkeypatch):
    for relative in proof.TOOL_SOURCES:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(relative, encoding="utf-8")
    monkeypatch.setattr(proof, "G3D", tmp_path)
    before = proof.tool_hashes()
    (tmp_path / "g3d/export_glb.py").write_text("unrelated exporter change", encoding="utf-8")
    assert proof.tool_hashes() == before
    (tmp_path / "g3d/seat_bpy.py").write_text("changed seat implementation", encoding="utf-8")
    after = proof.tool_hashes()
    assert after["g3d/seat_bpy.py"] != before["g3d/seat_bpy.py"] and after["*"] != before["*"]
