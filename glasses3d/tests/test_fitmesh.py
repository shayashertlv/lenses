"""GLB -> camera-fitting mesh (WP3b ``fitmesh``)."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from glasses3d.g3d_host import camera as C
from glasses3d.g3d_host import fitmesh
from glasses3d.tests import glbfab

G3D = Path(__file__).resolve().parents[1]
REF = G3D / "data" / "reference" / "projection"


def write(tmp_path, data, name="m.glb"):
    p = tmp_path / name
    p.write_bytes(data)
    return p


def test_loads_millimetres_parts_and_bbox_frame(tmp_path):
    m = fitmesh.load_fit_mesh(write(tmp_path, glbfab.build()))
    assert m.names == ["Bridge", "Brow_L", "Brow_R", "Lens_L", "Lens_R", "Temple_L", "Temple_R"]
    np.testing.assert_allclose(m.V.min(0), [-70, -25, -140], atol=1e-6)
    np.testing.assert_allclose(m.V.max(0), [70, 10, 2], atol=1e-6)
    assert m.F.shape == (7 * 12, 3) and m.part.shape == (84,) and m.F.max() == len(m.V) - 1
    assert np.array_equal(np.bincount(m.part), [12] * 7)
    np.testing.assert_allclose(m.frame.center, (0.0, -7.5, -69.0), atol=1e-6)
    assert m.frame.extent == pytest.approx(142.0)
    assert m.materials[0] is not None and m.skipped == []
    scene = m.scene()
    assert scene.n_faces == 84


def test_skip_given_frame_and_names_with_suffixes(tmp_path):
    parts = glbfab.default_parts()
    parts["Lens_L.001"] = parts.pop("Lens_L")
    fr = C.NormFrame((0.0, 0.0, 0.0), 200.0)
    m = fitmesh.load_fit_mesh(write(tmp_path, glbfab.build(parts)), skip=("Temple",), frame=fr)
    assert m.frame == fr
    assert "Lens_L" in m.names and "Lens_L.001" in m.node_names
    assert m.skipped == ["Temple_L", "Temple_R"] and len(m.names) == 5
    assert m.V[:, 2].min() == pytest.approx(-2.0)


def test_front_pivot_named_and_geometric(tmp_path):
    m = fitmesh.load_fit_mesh(write(tmp_path, glbfab.build()))
    piv = m.front_pivot(["Bridge", "Brow_L", "Brow_R", "Lens_L", "Lens_R"])
    np.testing.assert_allclose(m.frame.to_mm(piv[None])[0], [0.0, -7.5, 0.0], atol=1e-6)
    geo = m.front_pivot()                                   # faces within 12 mm of the front: the same front piece
    np.testing.assert_allclose(m.frame.to_mm(geo[None])[0], [0.0, -7.5, 0.0], atol=1e-6)
    with pytest.raises(ValueError, match="Nope"):
        m.front_pivot(["Nope"])
    # ONE missing name is an error too (the prototype's names.index raised): a smaller set would move the pivot
    with pytest.raises(ValueError, match=r"\['Saddle'\]"):
        m.front_pivot(["Bridge", "Brow_L", "Brow_R", "Lens_L", "Lens_R", "Saddle"])


def test_embedded_periods_keep_parts_distinct(tmp_path):
    parts = glbfab.default_parts()
    parts['component.00'] = parts.pop('Brow_L')
    parts['component.01'] = parts.pop('Brow_R')
    parts['temple.left.001'] = parts.pop('Temple_L')
    path = write(tmp_path, glbfab.build(parts))
    mesh = fitmesh.load_fit_mesh(path)
    assert mesh.part_ids(['component.00']) != mesh.part_ids(['component.01'])
    assert len(mesh.part_ids(['component.00', 'component.01'])) == 2
    assert 'temple.left' in mesh.names
    from types import SimpleNamespace
    from glasses3d.g3d_host.photoprint.project import part_materials
    materials = part_materials(SimpleNamespace(sources={'glb': path}))
    assert {'component.00', 'component.01', 'temple.left'} <= materials.keys()
    assert 'component' not in materials


def test_refuses_compressed_and_skips_non_triangles(tmp_path):
    with pytest.raises(ValueError, match="KHR_draco"):
        fitmesh.load_fit_mesh(write(tmp_path, glbfab.build(compressed=True)))
    m = fitmesh.load_fit_mesh(write(tmp_path, glbfab.build(mode=1), "lines.glb"))
    assert m.skipped == ["Bridge"] and "Bridge" not in m.names
    with pytest.raises(ValueError, match="no triangle geometry"):
        fitmesh.load_fit_mesh(write(tmp_path, glbfab.build(), "all.glb"), skip=("_", "Bridge"))


@pytest.mark.skipif(not (REF / "q001.glb").exists(), reason="reference data missing")
def test_q001_geometric_front_pivot_is_near_the_named_one():
    """The geometric fallback (faces within 12 mm of the front) on a real model: x and y agree exactly, z lands
    0.42 mm behind the named pivot (it also takes the nose pads, pad mounts and front hinges)."""
    m = fitmesh.load_fit_mesh(REF / "q001.glb", skip=fitmesh.PROTOTYPE_SKIP)
    named = m.frame.to_mm(m.front_pivot(fitmesh.PROTOTYPE_FRONT_PARTS)[None])[0]
    geo = m.frame.to_mm(m.front_pivot()[None])[0]
    np.testing.assert_allclose(geo[:2], named[:2], atol=1e-9)
    assert -0.5 < geo[2] - named[2] < -0.3, (geo, named)
