"""The Blender camera built by the harness must reproduce the host camera model (reconstruction.camera.project):
the same Camera + NormFrame rendered in Blender and rasterized by the host give the same silhouette."""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

import numpy as np
from PIL import Image
import pytest

from modeler import export as mexport
from modeler.paths import blender_executable
from modeler.worker import run_harness
from reconstruction.camera import Camera, render_mask
from reconstruction.mesh import TriangleMesh

PROGRAM = '''
outer = gl.rounded_rect(120.0, 44.0, 8.0, n=120)
holeR = gl.rounded_rect(46.0, 32.0, 7.0, n=64, center=(29.0, -1.0))
holeL = gl.rounded_rect(46.0, 32.0, 7.0, n=64, center=(-29.0, -1.0))
front = gl.plate_with_holes(outer, [holeR, holeL], z_front=0.0, thickness=5.0, name="front", part="frame", component="front")
for side, sx in (("R", 1.0), ("L", -1.0)):
    secs = [gl.section_rect((sx * 57.0, 8.0, z), 4.0, 7.0, (1, 0, 0), (0, 1, 0), n=16) for z in (-2.0, -60.0, -130.0)]
    gl.loft(secs, f"temple_{side}", f"temple_{side}", "arm")
'''


def iou(a, b):
    return float((a & b).sum()) / max(float((a | b).sum()), 1.0)


@pytest.mark.slow   # host Blender, ~16 s set-up
@unittest.skipIf(blender_executable() is None, "Blender not installed")
class CameraEquivalence(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        prog = root / "p.py"
        prog.write_text(PROGRAM, encoding="utf-8")
        # first pass: build + export (parts in the Blender world frame, mm)
        r = run_harness({"modules": [{"name": "p", "path": str(prog)}], "export": True, "save_blend": True, "renders": []},
                        root / "build", time_limit_s=180)
        assert r["ok"], r
        objects, _, _ = mexport.load_parts(Path(r["parts_npz"]), Path(r["materials_json"]))
        V = np.vstack([o["V"] for o in objects.values()])
        F, off = [], 0
        for o in objects.values():
            F.append(o["F"] + off)
            off += len(o["V"])
        F = np.vstack(F)
        lo, hi = V.min(0), V.max(0)
        cls.frame = {"center": ((lo + hi) / 2).tolist(), "extent": float((hi - lo).max())}
        cls.mesh = TriangleMesh((V - (lo + hi) / 2) / cls.frame["extent"], F, None)
        W, H = 400, 300
        cls.shape = (H, W)
        cls.cameras = {
            "persp": Camera(yaw=30.0, pitch=12.0, roll=6.0, perspective=0.25, scale=170.0, center_x=215.0, center_y=140.0),
            "ortho": Camera(yaw=-80.0, pitch=5.0, roll=-3.0, perspective=0.0, scale=150.0, center_x=190.0, center_y=160.0),
            "front": Camera(yaw=0.0, pitch=0.0, roll=0.0, perspective=0.12, scale=160.0, center_x=200.0, center_y=150.0),
        }
        renders = [{"id": k, "kind": "clay", "width": W, "height": H, "transparent": True,
                    "camera": {"type": "photo", "camera": c.to_dict(), "frame": cls.frame}} for k, c in cls.cameras.items()]
        r2 = run_harness({"mode": "render_only", "blend_path": r["blend"], "renders": renders, "samples": 4},
                         root / "render", time_limit_s=180)
        assert r2["ok"], r2
        cls.renders = {row["id"]: row["path"] for row in r2["renders"]}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _check(self, key, minimum=0.88, tolerant_minimum=0.97):
        """Two rasterizers quantize thin temples differently; the mapping is right when the silhouettes agree to
        within one pixel: bounding boxes within 2 px and the 1-px-dilated IoU high."""
        from scipy import ndimage
        alpha = np.asarray(Image.open(self.renders[key]).convert("RGBA"))[..., 3] > 64
        host = render_mask(self.mesh, self.cameras[key], self.shape)
        self.assertGreater(alpha.sum(), 300)
        for m, n in ((alpha, host), (host, alpha)):
            ys, xs = np.nonzero(m)
            ys2, xs2 = np.nonzero(n)
            for a, b in ((xs.min(), xs2.min()), (xs.max(), xs2.max()), (ys.min(), ys2.min()), (ys.max(), ys2.max())):
                self.assertLessEqual(abs(int(a) - int(b)), 2, f"{key}: silhouette bbox differs")
        self.assertGreater(iou(alpha, host), minimum, f"{key}: Blender vs host silhouette IoU too low")
        grown = ndimage.binary_dilation(host, iterations=1)
        grown_a = ndimage.binary_dilation(alpha, iterations=1)
        tolerant = float((alpha & grown).sum() + (host & grown_a).sum()) / float(alpha.sum() + host.sum())
        self.assertGreater(tolerant, tolerant_minimum, f"{key}: silhouettes differ by more than one pixel")

    def test_perspective(self):
        self._check("persp")

    def test_orthographic(self):
        self._check("ortho")

    def test_front(self):
        self._check("front")


if __name__ == "__main__":
    unittest.main()
