"""Step 4, the host's half of the Blender side: ``g3d.printuv.glbmesh`` (the arrays and identities g3d.print_uv and
the bake share) and ``g3d_host.photoprint.manifest`` (what g3d.attach_print reads; the staleness check). numpy only,
no Blender."""
import json
from pathlib import Path

import numpy as np
import pytest

from glasses3d.g3d import printuv as PU
from glasses3d.g3d.printuv import glbmesh as GM
from glasses3d.g3d_host import fitmesh
from glasses3d.g3d_host import glb as glbmod
from glasses3d.g3d_host.photoprint import manifest as MF

REPO = Path(__file__).resolve().parents[3]
Q001 = REPO / "glasses3d/data/reference/projection/q001.glb"
BAKE = REPO / "glasses3d/data/wp5/ft1119_bake"
CAMERAS = REPO / "glasses3d/data/wp3b/ft1119/cameras.json"
PARTS = ["Brow_Left", "Brow_Right", "Temple_Left", "Temple_Right"]
need_q001 = pytest.mark.skipif(not Q001.exists(), reason="q001.glb not present")


@pytest.fixture(scope="module")
def q001():
    return GM.mesh_arrays(GM.read_glb(Q001))


@need_q001
def test_read_glb_is_the_host_reader_exactly(q001):
    V, F, part, names = q001
    g = glbmod.load(Q001)
    prims = [p for p in g.primitives if p.mode == 4 and len(p.triangles)]
    assert names == [p.node_name.split(".")[0] for p in prims]          # Studio copies: 'Brow_Left.001'
    off = 0
    for i, p in enumerate(prims):
        f = np.nonzero(part == i)[0]
        assert np.array_equal(F[f] - off, p.triangles)
        assert np.array_equal(V[off:off + len(p.positions)], p.positions * 1000.0)
        off += len(p.positions)


@need_q001
def test_the_layout_of_glbmesh_arrays_is_the_bakes(q001):
    """g3d.print_uv lays out glbmesh arrays; the bake lays out the job mesh (decals skipped): same charts, same uvs."""
    V, F, part, names = q001
    model = json.loads(CAMERAS.read_text(encoding="utf-8"))["model"] if CAMERAS.exists() else {}
    fm = fitmesh.load_fit_mesh(Q001, skip=tuple(model.get("skip", ())))
    a = PU.layout(V, F, part, names, PARTS, 10.0)
    b = PU.layout(fm.V, fm.F, fm.part, fm.names, PARTS, 10.0)
    assert GM.charts_sha(a.charts, a.width, a.height) == GM.charts_sha(b.charts, b.width, b.height)
    for p in PARTS:
        ua = a.uv[a.face_index(len(F))[GM.part_faces(part, names, p)]]
        ub = b.uv[b.face_index(len(fm.F))[GM.part_faces(fm.part, fm.names, p)]]
        assert np.array_equal(ua, ub), p
    assert GM.geometry_sha(V, F, part, names, PARTS) == GM.geometry_sha(fm.V, fm.F, fm.part, fm.names, PARTS)


def _soup(V, F):
    """Every triangle with its own three vertices (the most split a glTF can be)."""
    return V[F].reshape(-1, 3), np.arange(len(F) * 3).reshape(-1, 3)


@need_q001
def test_geometry_sha_ignores_splits_order_and_start_corner(q001):
    V, F, part, names = q001
    ref = GM.geometry_sha(V, F, part, names, PARTS)
    sel = np.nonzero(np.isin(part, [names.index(p) for p in PARTS]))[0]
    Vs, Fs = _soup(V, F[sel])
    ps = part[sel]
    rng = np.random.default_rng(0)
    o = rng.permutation(len(Fs))
    Fr = Fs[o]
    roll = rng.integers(0, 3, len(Fr))
    Fr = np.stack([np.roll(f, r) for f, r in zip(Fr, roll)])                   # same winding, another start corner
    assert GM.geometry_sha(Vs, Fr, ps[o], names, PARTS) == ref
    flipped = Fr.copy()
    flipped[0] = flipped[0][::-1]                                              # winding matters
    assert GM.geometry_sha(Vs, flipped, ps[o], names, PARTS) != ref
    moved = Vs.copy()
    moved[0, 0] += 1e-6                                                        # any vertex move matters
    assert GM.geometry_sha(moved, Fr, ps[o], names, PARTS) != ref
    assert GM.geometry_sha(V, F, part, names, PARTS[::-1]) != ref             # the layout order is part of it
    assert GM.geometry_sha(np.where(V == 0.0, -0.0, V), F, part, names, PARTS) == ref       # -0.0 == +0.0


def test_suffix_stripping_only_strips_blender_copies():
    V = np.eye(3)
    F = np.array([[0, 1, 2]])

    def prim(name):
        return GM.Prim(node=name, mesh=0, index=0, material=None, positions_mm=V, triangles=F, normals=None)
    _, _, _, names = GM.mesh_arrays([prim("Brow_Left.001"), prim("Lens | clear"), prim("T.arm"), prim("A.12")])
    assert names == ["Brow_Left", "Lens | clear", "T.arm", "A.12"]


def test_charts_sha_sees_every_box_field():
    charts = [{"chart": 0, "part": "A", "index_in_part": 0, "kind": "strip", "faces": 10, "x0": 8, "y0": 8,
               "w": 30, "h": 12}]
    ref = GM.charts_sha(charts, 64, 32)
    for k in ("part", "kind", "faces", "x0", "y0", "w", "h", "index_in_part"):
        c = [dict(charts[0])]
        c[0][k] = c[0][k] + 1 if isinstance(c[0][k], int) else c[0][k] + "x"
        assert GM.charts_sha(c, 64, 32) != ref, k
    assert GM.charts_sha(charts, 65, 32) != ref
    assert GM.charts_sha([dict(charts[0], chart=5)], 64, 32) == ref                # the running id is not identity


@pytest.mark.skipif(not (BAKE / "layout.json").exists() or not Q001.exists(), reason="step-4 bake not present")
def test_manifest_of_the_step4_bake(q001):
    man = MF.build_manifest(BAKE)
    lay = json.loads((BAKE / "layout.json").read_text(encoding="utf-8"))
    V, F, part, names = q001
    assert man["geometry_sha"] == GM.geometry_sha(V, F, part, names, PARTS)
    assert man["charts_sha"] == GM.charts_sha(lay["charts"], lay["width"], lay["height"])
    assert (man["width"], man["height"]) == (lay["width"], lay["height"]) and man["parts"] == PARTS
    assert man["print_sha256"] == MF._sha(BAKE / "print.png") and man["layer"] == "g3d_print"
    rep = MF.check_glb(Q001, man)                                  # the bake's own GLB has no print layer yet
    assert not rep["ok"] and not rep["stale"]
    assert all("no g3d_print_uv" in r for r in rep["reasons"]) and len(rep["reasons"]) == 4


def test_manifest_refuses_a_changed_glb(tmp_path):
    """build_manifest re-hashes the bake's GLB and refuses when it changed since the bake."""
    if not (BAKE / "report.json").exists():
        pytest.skip("step-4 bake not present")
    d = tmp_path / "bake"
    d.mkdir()
    rep = json.loads((BAKE / "report.json").read_text(encoding="utf-8"))
    glb_copy = tmp_path / "model.glb"
    glb_copy.write_bytes(Path(rep["inputs"]["sources"]["glb"]).read_bytes() + b"\0\0\0\0")
    rep["inputs"]["sources"]["glb"] = str(glb_copy)
    (d / "report.json").write_text(json.dumps(rep), encoding="utf-8")
    for f in ("layout.json", "print.png"):
        (d / f).write_bytes((BAKE / f).read_bytes())
    with pytest.raises(ValueError, match="changed since the bake"):
        MF.build_manifest(d)


# ------------------------------------------------------------------- check_glb: what the GLB samples (fix round 1)
PAINTED = REPO / "glasses3d/data/wp5/tooltest/q001_painted_TOOLTEST.glb"


def _painted_or_skip():
    """The q001 TOOL TEST's painted GLB, when it was made from the current bake (its image is print.png)."""
    if not (PAINTED.exists() and (BAKE / MF.MANIFEST).exists()):
        pytest.skip("tool-test GLB / bake manifest not present")
    man = MF.load_manifest(BAKE)
    doc, binc = GM.chunks(PAINTED.read_bytes())
    data = MF._image_bytes(doc, binc, 0) if doc.get("images") else None
    import hashlib
    if data is None or hashlib.sha256(data).hexdigest() != man["print_sha256"]:
        pytest.skip("the tool-test GLB was made from an earlier bake (rerun tests/photoprint/print_tooltest.py)")
    return man


def test_check_glb_accepts_the_tool_test_glb():
    man = _painted_or_skip()
    r = MF.check_glb(PAINTED, man)
    assert r["ok"] and not r["stale"] and r["notes"] == [], r["reasons"]
    for p in PARTS:
        row = r["parts"][p]
        assert row["print_image"] == "bytes" and row["uv_checked"] and row["uv_max_err_texel"] < MF.UV_TOL_TEXEL


def test_check_glb_refuses_another_print_on_the_same_layout():
    """Review (fix round 1): a GLB carrying ANOTHER bake's print on the same layout reported ok."""
    man = dict(_painted_or_skip(), print_sha256="0" * 64, print_png=str(BAKE / "classes.png"))
    r = MF.check_glb(PAINTED, man, check_uv=False)
    assert not r["ok"] and all(r["parts"][p]["print_image"] == "other" for p in PARTS)
    assert sum("not the bake's print.png" in x for x in r["reasons"]) == 4


def test_check_glb_refuses_texcoords_edited_after_print_uv(tmp_path):
    """Review (fix round 1): uvs edited after print_uv reported ok. Half a texel added to u of one part's TEXCOORD_k
    (written into the GLB's binary chunk; positions and identities untouched) is caught, the other parts stay clean."""
    man = _painted_or_skip()
    data = bytearray(PAINTED.read_bytes())
    doc, _ = GM.chunks(bytes(data))
    jlen = int(np.frombuffer(bytes(data[12:16]), "<u4")[0])
    bin0 = 12 + 8 + jlen + 8
    node = next(n for n in doc["nodes"] if n.get("name", "").split(".")[0] == "Brow_Left" and "mesh" in n)
    k = json.loads(node["extras"]["g3d_print_uv"])["texcoord"]
    for prim in doc["meshes"][node["mesh"]]["primitives"]:
        acc = doc["accessors"][prim["attributes"][f"TEXCOORD_{k}"]]
        bv = doc["bufferViews"][acc["bufferView"]]
        off = bin0 + int(bv.get("byteOffset", 0)) + int(acc.get("byteOffset", 0))
        stride = int(bv.get("byteStride") or 8)
        for i in range(int(acc["count"])):
            u = np.frombuffer(bytes(data[off + i * stride:off + i * stride + 4]), "<f4")[0]
            data[off + i * stride:off + i * stride + 4] = np.float32(u + 0.5 / man["width"]).tobytes()
    edited = tmp_path / "edited.glb"
    edited.write_bytes(bytes(data))
    r = MF.check_glb(edited, man)
    assert not r["ok"] and r["stale"]
    assert [x for x in r["reasons"] if "TEXCOORD" in x and "Brow_Left" in x]
    assert r["parts"]["Brow_Left"]["uv_max_err_texel"] > 0.4
    assert all(r["parts"][p]["uv_max_err_texel"] < MF.UV_TOL_TEXEL for p in PARTS[1:])
