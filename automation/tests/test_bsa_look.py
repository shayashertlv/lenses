"""bsa.look: the S11 look stage (schema, patch guards, lens compiler, session, drivers, the editor's images). Hermetic:
synthetic export, a fake AR harness (texture.ar_render / archeck.run are patched; the fit renders carry exact
cameras so the pixel labels are real ray casts), a temp BSA_DATA; no network, no browser."""
import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
from PIL import Image, ImageDraw

from bsa import archeck, contract, core, export, pipeline, texture
from bsa import look as L
from reconstruction.lens_appearance import LensAppearance
from reconstruction.segmented_astra_job import run_astra_job
from reconstruction.segmented_astra_transport import PROMPT, AstraClient, validate_tools_schema
from test_segmented_astra_transport import HTTP, result

PROD = "fixture"
RUN = "_unittest_look"
MAGENTA = (255, 0, 255)                 # the held-out photo's colour: must never reach an editor image
IMAGE_IDS = ["fit-front", "fit-back", "fit-left", "fit-right", "material-map", "lens", "tryon-current"]


def _desc(keys, r0=(0.04, 0.04, 0.04), angular=None, rear=None, roughness=0.05):
    d = {"schema_version": 1, "color_space": "scene_linear_srgb_D65",
         "density_interpolation": "piecewise_smoothstep_optical_density",
         "vertical_coordinate": "lens_local_bottom_0_top_1", "normal_reflectance_rgb": list(r0),
         "refractive_index": 1.5, "roughness": roughness,
         "optical_density_keyframes": [{"v": v, "optical_density_rgb": list(d)} for v, d in keys],
         "angular_reflectance_keyframes": angular}
    if rear is not None:
        d["rear_reflection_fraction_rgb"] = list(rear)
    return LensAppearance.from_dict(d).to_dict()


GRADIENT = _desc([(0.0, (0.3, 0.35, 0.45)), (0.5, (0.9, 1.0, 1.2)), (1.0, (1.4, 1.6, 2.0))])
UNIFORM = _desc([(0.0, (1.0, 1.0, 1.0))])
MIRROR = _desc([(0.0, (0.5, 0.4, 0.3))], r0=(0.2, 0.5, 0.6),
               angular=[{"angle_degrees": 0.0, "reflectance_rgb": [0.2, 0.5, 0.6]},
                        {"angle_degrees": 30.0, "reflectance_rgb": [0.25, 0.45, 0.62]},
                        {"angle_degrees": 60.0, "reflectance_rgb": [0.4, 0.5, 0.7]},
                        {"angle_degrees": 90.0, "reflectance_rgb": [1.0, 1.0, 1.0]}], rear=(0.04, 0.04, 0.04))
# the measured m2 S9 lenses (S8's fits) the 2026-09-25 review broke the compiler with: INVU's angle table is not
# monotone (0.58 head-on, 0.21 blue at 52 deg, 0.57 at 70; blue's 70-deg gap to head-on is only -0.015) and OAKLEY's
# density profile is lighter in the middle than at either end (green 1.62 -> 1.40 -> 1.58)
_V16 = (0.0, 0.063492, 0.126984, 0.206349, 0.269841, 0.333333, 0.396825, 0.460317, 0.539683, 0.603175, 0.666667,
        0.730159, 0.793651, 0.873016, 0.936508, 1.0)
INVU = _desc(list(zip(_V16, (
    (1.53433, 0.787101, 0.012141), (1.395499, 0.684776, 0.044041), (1.225081, 0.564127, 0.079443),
    (1.094301, 0.515538, 0.106092), (1.013468, 0.55117, 0.14681), (0.999533, 0.577683, 0.176396),
    (1.016719, 0.588554, 0.199263), (0.990132, 0.601779, 0.23096), (0.98975, 0.600742, 0.305715),
    (1.060759, 0.602052, 0.341182), (1.123605, 0.590865, 0.354848), (1.198948, 0.577785, 0.338317),
    (1.308896, 0.547207, 0.288679), (1.5049, 0.513236, 0.184432), (1.709322, 0.49978, 0.133414),
    (1.82634, 0.499652, 0.130483)))), r0=(0.5124, 0.4825, 0.5827), rear=(0.04, 0.04, 0.04), angular=[
    {"angle_degrees": a, "reflectance_rgb": list(r)} for a, r in (
        (0.0, (0.5124, 0.4825, 0.5827)), (7.6686, (0.5124, 0.4825, 0.5827)), (12.6641, (0.4014, 0.4065, 0.5535)),
        (17.6707, (0.332, 0.2037, 0.4185)), (22.3697, (0.229, 0.1807, 0.3092)), (27.4253, (0.1998, 0.2268, 0.2581)),
        (32.39, (0.2746, 0.1604, 0.2655)), (37.4029, (0.3323, 0.2496, 0.2914)), (42.3938, (0.4192, 0.3249, 0.2914)),
        (47.4679, (0.5312, 0.2294, 0.2713)), (52.3026, (0.5889, 0.1491, 0.2077)), (90.0, (1.0, 1.0, 1.0)))])
OAKLEY = _desc(list(zip(_V16, (
    (1.193942, 1.61791, 1.300585), (1.193834, 1.617734, 1.300407), (1.180816, 1.595149, 1.275153),
    (1.138458, 1.461521, 1.236914), (1.123062, 1.416347, 1.216651), (1.119123, 1.398077, 1.214362),
    (1.119111, 1.411573, 1.214361), (1.119111, 1.412377, 1.214361), (1.119111, 1.412377, 1.214361),
    (1.119111, 1.412397, 1.214361), (1.119111, 1.41973, 1.214345), (1.119111, 1.446316, 1.209173),
    (1.119672, 1.470742, 1.201155), (1.145934, 1.516002, 1.22636), (1.203571, 1.554091, 1.256169),
    (1.237057, 1.578195, 1.285611)))), r0=(0.0, 0.6255, 0.4562), rear=(0.04, 0.04, 0.04), angular=[
    {"angle_degrees": a, "reflectance_rgb": list(r)} for a, r in (
        (0.0, (0.0, 0.6255, 0.4562)), (8.3053, (0.0, 0.6255, 0.4562)), (12.8142, (0.001, 0.8381, 0.4268)),
        (17.7276, (0.0061, 0.9025, 0.3962)), (22.4943, (0.0552, 0.9025, 0.4896)), (27.5526, (0.0306, 0.9025, 0.5807)),
        (32.2091, (0.1146, 0.609, 0.4351)), (37.3466, (0.0929, 0.4679, 0.6078)), (42.3751, (0.1596, 0.259, 0.8314)),
        (47.3415, (0.2139, 0.2005, 0.8314)), (52.381, (0.2369, 0.1941, 0.8313)), (56.8073, (0.255, 0.2094, 0.8062)),
        (60.4521, (0.2372, 0.2777, 0.7235)), (90.0, (1.0, 1.0, 1.0)))])
HELD_OUT_REASON = "failed:c3_heldout_angled_front_piece"


def knot_T(desc, angle=0.0):
    la = LensAppearance.from_dict(desc)
    return np.asarray(la.evaluate(np.asarray([k.v for k in la.optical_density_keyframes]), angle).transmission_rgb, float)


def table(desc):
    return {r["angle_degrees"]: np.asarray(r["reflectance_rgb"], float) for r in desc["angular_reflectance_keyframes"]}


def fixture_glb(path: Path) -> bytes:
    parts, mats = export.synthetic_parts()
    mats["lens"].pop("base_color_texture", None)
    mats["lens"]["lens_appearance"] = GRADIENT
    mats["lens"]["base_color"] = [0.3, 0.25, 0.2, 1.0]
    mats["frame"]["base_color"] = [0.4, 0.3, 0.9, 1.0]
    mats["temple"]["base_color"] = [0.5, 0.4, 0.3, 1.0]
    export.write_glb(parts, mats, path)
    return path.read_bytes()


def plan(*ops, note="fixture step"):
    return {"note": note, "operations": list(ops)}


def frame_op(material="all_frame", ratio=None, roughness=None, metallic=None):
    return {"operation": "frame_material", "material": material, "color_ratio_rgb": ratio, "roughness": roughness,
            "metallic": metallic}


def lens_op(lens="all", top=(0.2, 0.2, 0.2), bottom=(0.2, 0.2, 0.2), r0=(0.04, 0.04, 0.04), grazing=None, roughness=0.05):
    return {"operation": "lens_look", "lens": lens, "transmission_rgb_top": list(top),
            "transmission_rgb_bottom": list(bottom), "normal_reflectance_rgb": list(r0),
            "grazing_reflectance_rgb": None if grazing is None else list(grazing), "roughness": roughness}


def finish(verdict="improved", note="closer to the photos", deliver=None):
    return plan({"operation": "finish", "verdict": verdict, "note": note, "deliver_revision": deliver})


FINISH = finish()


def camera(eye, W=720, H=480, fovy=30.0) -> dict:
    """A three.js-style camera looking at the asset origin: row-major P, V and A2W, as ``texture.ar_render`` returns."""
    eye = np.asarray(eye, float)
    z = eye / np.linalg.norm(eye)
    x = np.cross([0.0, 1.0, 0.0], z)
    x /= np.linalg.norm(x)
    y = np.cross(z, x)
    V = np.eye(4)
    V[0, :3], V[1, :3], V[2, :3] = x, y, z
    V[:3, 3] = -V[:3, :3] @ eye
    f, n, far = 1.0 / np.tan(np.radians(fovy) / 2), 0.01, 10.0
    P = np.array([[f * H / W, 0, 0, 0], [0, f, 0, 0], [0, 0, (far + n) / (n - far), 2 * far * n / (n - far)], [0, 0, -1, 0]])
    return {"P": P, "V": V, "A2W": np.eye(4), "W": W, "H": H}


# the real harness: yaw +80 puts the camera at -X, yaw -80 at +X (a fixed "yaw +80 = left" rule would be wrong)
CAMS = {"front": [0, 0, 0.4], "back": [0, 0, -0.4], "side_pos": [-0.4, 0, 0], "side_neg": [0.4, 0, 0]}


class FakeHarness:
    """Stand-ins for the actual AR runtime: white / checker PNGs with the frame's factor colour drawn in, exact
    cameras for the fit views, and the report fields bsa.look reads. ``fail_tryon_after`` n: the (n+1)-th try-on
    run reports a rejected model. ``no_cameras``: the fit views report no camera matrices."""
    fail_tryon_after = None
    no_cameras = False
    tryon_calls = 0
    fit_calls = 0

    @staticmethod
    def _colour(data: bytes):
        doc, _ = texture.glb_split(data)
        f = next(m for m in doc["materials"] if m["name"] == "frame")["pbrMetallicRoughness"].get("baseColorFactor", [1, 1, 1, 1])
        return tuple(int(40 + 150 * c) for c in f[:3])

    @classmethod
    def ar_render(cls, glbs, out_dir, views=texture.AR_FIT_VIEWS, background_rgb=(255, 255, 255), **kw):
        cls.fit_calls += 1
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        res = {"models": {}, "harness_status": "passed", "returncode": 0}
        for name, data in glbs.items():
            vw = {}
            for v in views:
                im = Image.new("RGB", (720, 480), tuple(background_rgb))
                ImageDraw.Draw(im).rectangle([200, 180, 520, 300], fill=cls._colour(data))
                p = out_dir / f"{archeck.safe_id(name)}__actual-ar__{v['id']}.png"
                im.save(p)
                vw[v["id"]] = {"png": str(p), "camera_in_asset": np.array(CAMS[v["id"]], float),
                               **({} if cls.no_cameras else camera(CAMS[v["id"]]))}
            res["models"][name] = {"status": "runtime_compatible", "optical_meshes": 2, "error": None, "views": vw}
        return res

    @classmethod
    def run(cls, glb_paths, out_dir, *, ar_views=archeck.DEFAULT_AR_VIEWS, **kw):
        cls.tryon_calls += 1
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        fail = cls.fail_tryon_after is not None and cls.tryon_calls > cls.fail_tryon_after
        models = {}
        for name, p in glb_paths.items():
            data = Path(p).read_bytes()
            renders = []
            for v in ar_views:
                im = Image.fromarray(archeck.checker_background())
                ImageDraw.Draw(im).rectangle([240, 200, 480, 280], fill=cls._colour(data))
                q = out_dir / f"{archeck.safe_id(name)}__actual-ar__{v['id']}.png"
                im.save(q)
                renders.append(str(q))
            n = len(L.glb_inventory(data)["lens_nodes"])
            models[name] = {"status": "runtime_rejected" if fail else "runtime_compatible", "runtime_compatible": not fail,
                            "error": "fixture rejection" if fail else None, "optical_meshes_detected": n,
                            "renders": renders, "synthetic_fit_ready": True, "continuity_failure": None}
        return {"harness_status": "failed" if fail else "passed", "returncode": 0, "models": models}


class LookFixture(unittest.TestCase):
    """A temp BSA_DATA with runs/_unittest_look/fixture/{s0,s8,s9,s10} and product photos (incl. a magenta held-out
    angled photo); the AR harness faked."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        photos = self.root / "photos"
        photos.mkdir()
        for v in core.VIEWS:
            im = Image.new("RGB", (640, 360), MAGENTA if v == "angled" else (248, 248, 248))
            if v != "angled":
                ImageDraw.Draw(im).rectangle([120, 120, 520, 240], fill=(70, 45, 35))
            im.save(photos / f"{v}.jpg", quality=95)
        FakeHarness.fail_tryon_after, FakeHarness.tryon_calls, FakeHarness.fit_calls = None, 0, 0
        FakeHarness.no_cameras = False
        for p in (mock.patch.object(core, "BSA_DATA", self.root / "data"),
                  mock.patch.dict(core.PRODUCTS, {PROD: core.Product(PROD, photos, photos / "none.glb", None, "fixture kind text")}),
                  mock.patch.object(texture, "ar_render", FakeHarness.ar_render),
                  mock.patch.object(archeck, "run", FakeHarness.run)):
            p.start()
            self.addCleanup(p.stop)
        rd = core.run_dir(RUN, PROD)
        (rd / "s9_export").mkdir(parents=True)
        self.s9 = fixture_glb(rd / "s9_export" / "model.glb")
        (rd / "s9_export" / "result.json").write_text("{}")
        core.stage_dir(RUN, PROD, "s10_gate").save({"decision": {"decision": "READY", "reasons": [
            "info:front_low_resolution", HELD_OUT_REASON]}, "flags": ["angled_contour_note"]})
        core.stage_dir(RUN, PROD, "s8_lens").save({"class": "gradient", "flags": [], "lens_colour_check": {
            "rendered_dE00": 3.2, "bands": [{"band": 0, "dE00": 3.2, "photo_srgb": [90, 80, 70], "render_srgb": [95, 82, 71]}]}})
        core.stage_dir(RUN, PROD, "s0_intake").save({"views": {v: {"bbox_xyxy": [120, 120, 521, 241]} for v in core.VIEWS}})
        self.sd = core.run_dir(RUN, PROD) / L.STAGE

    def main(self, *args):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            res = L.main(["--run", RUN, "--product", PROD, *args])
        return res, out.getvalue()

    def script(self, *plans) -> Path:
        p = self.root / f"script-{hashlib.sha256(json.dumps(plans).encode()).hexdigest()[:8]}.json"
        p.write_text(json.dumps(list(plans)))
        return p

    def turn_input(self, n: int) -> dict:
        return json.loads((self.sd / "session" / "turns" / f"turn-{n:04d}" / "input.json").read_text())

    def observation(self, rid: str = "r0000") -> dict:
        state = json.loads((self.sd / "session" / "state.json").read_text())
        rev = json.loads(Path(state["revisions"][rid]["path"]).read_text())
        return json.loads(Path(rev["observation"]["path"]).read_text())

    def sent_images(self) -> list[Path]:
        paths = []
        for f in sorted((self.sd / "session" / "turns").glob("turn-*/input.json")):
            paths += [Path(i["path"]) for i in json.loads(f.read_text())["images"]]
        return paths

    def assert_no_held_out(self, paths):
        self.assertTrue(paths)
        for p in paths:
            a = np.asarray(Image.open(p).convert("RGB")).astype(int)
            magenta = (a[..., 0] > 200) & (a[..., 1] < 70) & (a[..., 2] > 200)
            self.assertEqual(int(magenta.sum()), 0, f"held-out photo pixels in {p.name}")


class SchemaAndPatchTest(LookFixture):
    def test_schema_is_strict_and_product_specific(self):
        inv = L.glb_inventory(self.s9)
        schema = L.build_tools_schema(inv)
        validate_tools_schema(schema)
        self.assertLess(len(json.dumps(schema)), 128 * 1024)
        ops = {b["properties"]["operation"]["enum"][0]: b for b in schema["properties"]["operations"]["items"]["anyOf"]}
        self.assertEqual(set(ops), {"frame_material", "lens_look", "restore", "finish"})
        self.assertEqual(ops["frame_material"]["properties"]["material"]["enum"], ["frame", "temple", "all_frame"])
        self.assertEqual(ops["lens_look"]["properties"]["lens"]["enum"], ["all", "lens_L", "lens_R"])
        self.assertEqual(set(ops["finish"]["properties"]), {"operation", "verdict", "note", "deliver_revision"})
        for b in ops.values():                         # strict: every property required, nothing extra
            self.assertEqual(set(b["required"]), set(b["properties"]))
            self.assertIs(b["additionalProperties"], False)
        L.validate_look(plan(frame_op(roughness=0.3)), schema)
        L.validate_look(plan(lens_op()), schema)
        L.validate_look(finish(deliver="r0003"), schema)
        for bad in (plan(frame_op()),                                         # no factor
                    plan(frame_op(roughness=0.3), FINISH["operations"][0]),   # finish not alone
                    plan(frame_op(material="lens", roughness=0.3)),           # a lens material is not a frame material
                    plan(frame_op(ratio=[3, 1, 1])),                          # ratio bound
                    plan(lens_op(top=(0.001, 0.2, 0.2))),                     # transmission bound
                    finish(deliver="latest"),                                 # deliver_revision is a revision id
                    plan({"operation": "finish", "verdict": "improved", "note": "x"})):   # deliver_revision required
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                L.validate_look(bad, schema)

    def test_the_energy_rule_is_stated_in_prompt_schema_and_limits(self):
        schema = L.build_tools_schema(L.glb_inventory(self.s9))
        lens = next(b for b in schema["properties"]["operations"]["items"]["anyOf"]
                    if b["properties"]["operation"]["enum"] == ["lens_look"])
        self.assertIn(L.ENERGY_RULE, lens["description"])
        for k in ("transmission_rgb_top", "transmission_rgb_bottom"):
            self.assertIn("+ normal_reflectance_rgb must be <= 1", lens["properties"][k]["description"])
        self.assertEqual(lens["properties"]["grazing_reflectance_rgb"]["description"], L.GRAZING_RULE)
        self.assertIn("ENERGY: per channel, transmission (top and bottom) + head-on reflectance <= 1", L.LOOK_PROMPT)
        self.assertIn("applied_with_adjustment", L.LOOK_PROMPT)
        self.assertIn("deliver_revision", L.LOOK_PROMPT)

    def test_patch_keeps_geometry_and_changes_only_the_targeted_factor(self):
        inv = L.glb_inventory(self.s9)
        look, adjust = L.apply_operations(L.empty_look(), [frame_op("frame", roughness=0.3)], inv,
                                          L.s9_frame_factors(self.s9, inv), L.s9_lens_descriptors(self.s9, inv))
        self.assertEqual(adjust, [])
        out = L.apply_look(self.s9, look, inv)
        geo = L.geometry_check(L.geometry_fingerprint(self.s9), out, hashlib.sha256(texture.glb_split(self.s9)[1]).hexdigest())
        self.assertTrue(geo["ok"] and geo["bin_chunk_identical"], geo)
        self.assertGreater(geo["checked"], 20)
        a, b = texture.glb_split(self.s9)[0], texture.glb_split(out)[0]
        self.assertEqual(b["extras"].pop("bsa_look")["s9_glb_sha256"], hashlib.sha256(self.s9).hexdigest())
        changed = [(i, k) for i, (ma, mb) in enumerate(zip(a["materials"], b["materials"]))
                   for k in set(ma["pbrMetallicRoughness"]) | set(mb["pbrMetallicRoughness"])
                   if ma["pbrMetallicRoughness"].get(k) != mb["pbrMetallicRoughness"].get(k)]
        self.assertEqual(changed, [(0, "roughnessFactor")])
        self.assertEqual(b["materials"][0]["pbrMetallicRoughness"]["roughnessFactor"], 0.3)
        b["materials"][0]["pbrMetallicRoughness"]["roughnessFactor"] = a["materials"][0]["pbrMetallicRoughness"]["roughnessFactor"]
        self.assertEqual(a, b)                          # nothing else in the JSON moved
        p = self.root / "patched.glb"
        p.write_bytes(out)
        self.assertTrue(contract.check(p)["ok"])
        # a moved vertex is caught by the guard
        doc, binary = texture.glb_split(out)
        acc = doc["accessors"][doc["meshes"][0]["primitives"][0]["attributes"]["POSITION"]]
        off = doc["bufferViews"][acc["bufferView"]].get("byteOffset", 0)
        moved = bytearray(binary)
        moved[off:off + 4] = np.float32(1.0).tobytes()
        self.assertFalse(L.geometry_check(L.geometry_fingerprint(self.s9), texture.glb_pack(doc, bytes(moved)))["ok"])

    def test_color_ratio_multiplies_the_current_factor_and_clips(self):
        inv = L.glb_inventory(self.s9)
        f9 = L.s9_frame_factors(self.s9, inv)
        look, adjust = L.apply_operations(L.empty_look(), [frame_op("frame", ratio=[1.5, 0.5, 1.5]),
                                                           frame_op("frame", ratio=[0.5, 1.0, 1.0])],
                                          inv, f9, L.s9_lens_descriptors(self.s9, inv))
        self.assertEqual(look["materials"]["frame"]["baseColorFactor"], [0.3, 0.15, 1.0, 1.0])   # 0.4*1.5*0.5, 0.3*0.5, clip
        self.assertEqual(len(adjust), 1)
        self.assertEqual((adjust[0]["kind"], adjust[0]["changes_request"]), ("base_color_clip", True))
        self.assertEqual(adjust[0]["clipped_channels"], ["B"])
        self.assertAlmostEqual(adjust[0]["requested_factor"][2], 1.35)
        self.assertNotIn("temple", look["materials"])
        look2, _ = L.apply_operations(L.empty_look(), [frame_op("all_frame", ratio=[1.0, 1.0, 0.5])], inv, f9, {})
        self.assertEqual(sorted(look2["materials"]), ["frame", "temple"])
        self.assertEqual(look2["materials"]["temple"]["baseColorFactor"], [0.5, 0.4, 0.15, 1.0])

    def test_single_lens_edit_clones_its_material_only(self):
        inv = L.glb_inventory(self.s9)
        look, _ = L.apply_operations(L.empty_look(), [lens_op("lens_R", top=(0.1, 0.1, 0.1), bottom=(0.5, 0.5, 0.5))],
                                     inv, L.s9_frame_factors(self.s9, inv), L.s9_lens_descriptors(self.s9, inv))
        self.assertEqual(sorted(look["lenses"]), ["lens_R"])
        out = L.apply_look(self.s9, look, inv)
        doc, _ = texture.glb_split(out)
        mat = {n["name"]: doc["materials"][doc["meshes"][n["mesh"]]["primitives"][0]["material"]] for n in doc["nodes"]}
        self.assertEqual(mat["lens_L"]["name"], "lens")
        self.assertEqual(mat["lens_R"]["name"], "lens__lens_R")
        self.assertEqual(mat["lens_L"]["extensions"][L.LENS_APPEARANCE_EXTENSION]["appearance"], GRADIENT)
        self.assertEqual(mat["lens_R"]["extensions"][L.LENS_APPEARANCE_EXTENSION]["appearance"], look["lenses"]["lens_R"]["appearance"])
        self.assertEqual(mat["lens_R"]["pbrMetallicRoughness"]["baseColorFactor"][:3], look["lenses"]["lens_R"]["fallback_base_color"])
        self.assertTrue(L.geometry_check(L.geometry_fingerprint(self.s9), out)["ok"])
        p = self.root / "one-lens.glb"
        p.write_bytes(out)
        self.assertTrue(contract.check(p)["ok"])


    def test_the_guard_locks_the_whole_document_outside_the_editable_fields(self):
        inv = L.glb_inventory(self.s9)
        f9, l9 = L.s9_frame_factors(self.s9, inv), L.s9_lens_descriptors(self.s9, inv)
        look, _ = L.apply_operations(L.empty_look(), [frame_op("frame", ratio=[0.9, 1.0, 1.1], roughness=0.3),
                                                      lens_op("lens_R", top=(0.1, 0.1, 0.1), bottom=(0.5, 0.5, 0.5))],
                                     inv, f9, l9)
        out = L.apply_look(self.s9, look, inv)
        ref = L.geometry_fingerprint(self.s9)
        self.assertIn("document", ref)
        self.assertTrue(L.geometry_check(ref, out)["ok"])            # factors, a lens clone, extras: allowed
        doc, binary = texture.glb_split(out)
        frame = next(m for m in doc["materials"] if m["name"] == "frame")
        lens_prim = doc["meshes"][next(n for n in doc["nodes"] if n.get("name") == "lens_L")["mesh"]]["primitives"][0]
        mutations = {
            "texCoord": lambda d: frame["pbrMetallicRoughness"]["baseColorTexture"].update(texCoord=1),
            "texture_transform": lambda d: frame["pbrMetallicRoughness"]["baseColorTexture"].setdefault(
                "extensions", {}).update(KHR_texture_transform={"scale": [2, 2]}),
            "alpha_mode": lambda d: frame.update(alphaMode="BLEND"),
            "extra_attribute": lambda d: lens_prim["attributes"].update(TEXCOORD_1=lens_prim["attributes"]["TEXCOORD_0"]),
            "node_transform": lambda d: d["nodes"][0].update(translation=[0.0, 0.001, 0.0]),
            "sampler": lambda d: d.setdefault("samplers", []).append({"magFilter": 9728}),
            "lens_extension_field": lambda d: next(m for m in d["materials"] if m["name"] == "lens")["extensions"][
                L.LENS_APPEARANCE_EXTENSION].update(texcoord=1)}
        for name, mutate in mutations.items():
            with self.subTest(mutation=name):
                d = json.loads(json.dumps(doc))
                frame = next(m for m in d["materials"] if m["name"] == "frame")
                lens_prim = d["meshes"][next(n for n in d["nodes"] if n.get("name") == "lens_L")["mesh"]]["primitives"][0]
                mutate(d)
                res = L.geometry_check(ref, texture.glb_pack(d, binary))
                self.assertFalse(res["ok"])
                self.assertIn("document", res["mismatched"])

    def test_a_no_op_frame_edit_leaves_the_look_unchanged(self):
        inv = L.glb_inventory(self.s9)
        f9, l9 = L.s9_frame_factors(self.s9, inv), L.s9_lens_descriptors(self.s9, inv)
        for ops in ([frame_op(ratio=[1.0, 1.0, 1.0])], [frame_op("frame", roughness=f9["frame"]["roughnessFactor"])],
                    [frame_op("temple", metallic=f9["temple"]["metallicFactor"], ratio=[1, 1, 1])],
                    [lens_op(**{k: v for k, v in zip(("top", "bottom", "r0", "grazing", "roughness"),
                                                      L.lens_parameters(GRADIENT).values())})]):
            with self.subTest(ops=ops):
                look, adjust = L.apply_operations(L.empty_look(), ops, inv, f9, l9)
                self.assertEqual((look, adjust), (L.empty_look(), []))
        # an edit and its inverse normalise back to the empty look (S9 bytes), never a copy of S9 with rounded values
        look, _ = L.apply_operations(L.empty_look(), [frame_op("frame", roughness=0.3)], inv, f9, l9)
        back, _ = L.apply_operations(look, [frame_op("frame", roughness=f9["frame"]["roughnessFactor"])], inv, f9, l9)
        self.assertEqual(back, L.empty_look())

    def test_metallic_roughness_texture_medians_are_measured_on_the_material(self):
        parts, mats = export.synthetic_parts()
        mats["lens"].pop("base_color_texture", None)
        mats["lens"]["lens_appearance"] = GRADIENT
        mr = np.zeros((32, 32, 3), np.uint8)
        mr[..., 1], mr[..., 2] = 128, 255                     # roughness 0.502, metal 1.0
        mats["frame"]["metallic_roughness_texture"] = mr
        mats["frame"]["metallic"], mats["frame"]["roughness"] = 1.0, 0.5
        path = self.root / "mr.glb"
        export.write_glb(parts, mats, path)
        data = path.read_bytes()
        inv = L.glb_inventory(data)
        got = L.mr_texture_medians(data, inv)
        self.assertEqual(sorted(got), ["frame"])
        self.assertEqual((got["frame"]["roughness_texture_median"], got["frame"]["metallic_texture_median"]), (0.502, 1.0))
        self.assertGreater(got["frame"]["faces"], 100)
        self.assertEqual(got["frame"]["surface_groups"], [{"roughness": 0.5, "metallic": 1.0, "surface_share": 1.0}])

    def test_held_out_reasons_are_withheld_from_the_editor(self):
        s10 = {"decision": "RETRY", "reasons": ["failed:c2_lens_edge_vs_gt", HELD_OUT_REASON, "info:angled_x"],
               "flags": ["lens_colour_mismatch", "held_out_note"]}
        got = L.editor_s10(s10)
        self.assertEqual((got["decision"], got["reasons"], got["flags"]),
                         ("RETRY", ["failed:c2_lens_edge_vs_gt"], ["lens_colour_mismatch"]))
        self.assertNotIn("heldout", json.dumps(got).replace("held-out view", ""))


class LensCompilerTest(unittest.TestCase):
    def check(self, desc):
        LensAppearance.from_dict(desc)
        self.assertLessEqual(len(desc["optical_density_keyframes"]), L.MAX_KNOTS)
        if desc["angular_reflectance_keyframes"]:
            self.assertLessEqual(len(desc["angular_reflectance_keyframes"]), L.MAX_KNOTS)
            self.assertEqual(desc["angular_reflectance_keyframes"][0]["reflectance_rgb"], desc["normal_reflectance_rgb"])

    def test_restating_the_current_lens_is_a_no_op(self):
        for base in (GRADIENT, UNIFORM, MIRROR):
            with self.subTest(base=base["normal_reflectance_rgb"]):
                p = L.lens_parameters(base)
                # the grazing value is shown as a value (the 70-deg reflectance), never a null beside it
                np.testing.assert_allclose(p["grazing_reflectance_rgb"], L.lens_state(base)["reflectance_at_70deg_rgb"], atol=5e-5)
                out, notes = L.compile_lens(dict(operation="lens_look", lens="all", **p), base)
                self.assertEqual(out, base)
                self.assertEqual(notes, [])

    def test_uniform_gradient_and_mirror_compile_to_valid_descriptors(self):
        uni, notes = L.compile_lens(lens_op(top=(0.2, 0.3, 0.4), bottom=(0.2, 0.3, 0.4)), GRADIENT)
        self.check(uni)
        self.assertEqual(len(uni["optical_density_keyframes"]), 1)
        self.assertEqual([(n["kind"], n["knots_before"], n["knots_after"]) for n in notes], [("knots_changed", 3, 1)])
        np.testing.assert_allclose(L.lens_parameters(uni)["transmission_rgb_top"], [0.2, 0.3, 0.4], atol=1e-4)
        grad, notes = L.compile_lens(lens_op(top=(0.1, 0.1, 0.1), bottom=(0.6, 0.5, 0.4)), GRADIENT)
        self.check(grad)
        self.assertEqual(notes, [])
        self.assertEqual([k["v"] for k in grad["optical_density_keyframes"]], [0.0, 0.5, 1.0])   # measured shape kept
        p = L.lens_parameters(grad)
        np.testing.assert_allclose(p["transmission_rgb_top"], [0.1] * 3, atol=1e-4)
        np.testing.assert_allclose(p["transmission_rgb_bottom"], [0.6, 0.5, 0.4], atol=1e-4)
        grad2, notes = L.compile_lens(lens_op(top=(0.1, 0.1, 0.1), bottom=(0.6, 0.5, 0.4)), UNIFORM)
        self.assertEqual([k["v"] for k in grad2["optical_density_keyframes"]], [0.0, 1.0])
        self.assertEqual([(n["kind"], n["knots_before"], n["knots_after"]) for n in notes], [("knots_changed", 1, 2)])
        mir, notes = L.compile_lens(lens_op(top=(0.2, 0.2, 0.2), bottom=(0.2, 0.2, 0.2), r0=(0.3, 0.5, 0.7),
                                            grazing=(0.6, 0.7, 0.8)), UNIFORM)
        self.check(mir)
        # a Schlick lens (no table) gets a host table through the 70-deg value: the knot change is reported
        self.assertEqual([(n["kind"], n["table"], n["knots_before"], n["knots_after"]) for n in notes],
                         [("knots_changed", "angular", 0, 6)])
        table = {r["angle_degrees"]: r["reflectance_rgb"] for r in mir["angular_reflectance_keyframes"]}
        np.testing.assert_allclose(table[70.0], [0.6, 0.7, 0.8], atol=1e-6)
        self.assertEqual(table[90.0], [1.0, 1.0, 1.0])
        np.testing.assert_allclose(L.lens_parameters(mir)["transmission_rgb_top"], [0.2] * 3, atol=1e-4)

    def test_measured_angular_table_is_rescaled_not_replaced(self):
        cur = L.lens_parameters(MIRROR)
        for grazing in (None, cur["grazing_reflectance_rgb"]):      # null and a restated value both keep the shape
            with self.subTest(grazing=grazing):
                out, notes = L.compile_lens(lens_op(top=cur["transmission_rgb_top"], bottom=cur["transmission_rgb_bottom"],
                                                    r0=(0.1, 0.4, 0.5), grazing=grazing), MIRROR)
                self.check(out)
                self.assertEqual([r["angle_degrees"] for r in out["angular_reflectance_keyframes"]], [0.0, 30.0, 60.0, 90.0])
                self.assertEqual(out["angular_reflectance_keyframes"][-1]["reflectance_rgb"], [1.0, 1.0, 1.0])
                self.assertEqual(out["rear_reflection_fraction_rgb"], [0.04, 0.04, 0.04])
                self.assertEqual([n["kind"] for n in notes], ["angular_rescaled"])
                # what is seen through the lens is kept when only the mirror moves
                np.testing.assert_allclose(L.lens_parameters(out)["transmission_rgb_top"], cur["transmission_rgb_top"], atol=1e-4)

    def test_a_new_grazing_value_keeps_a_measured_table_shape_and_reports_the_knot(self):
        cur = L.lens_parameters(MIRROR)
        g = [0.5, 0.62, 0.8]
        out, notes = L.compile_lens(lens_op(top=cur["transmission_rgb_top"], bottom=cur["transmission_rgb_bottom"],
                                            r0=cur["normal_reflectance_rgb"], grazing=g), MIRROR)
        self.check(out)
        # the measured knots stay (a 70-deg knot is inserted), never replaced by the 6-knot host table
        self.assertEqual([r["angle_degrees"] for r in out["angular_reflectance_keyframes"]], [0.0, 30.0, 60.0, 70.0, 90.0])
        np.testing.assert_allclose(L.lens_state(out)["reflectance_at_70deg_rgb"], g, atol=1e-6)
        np.testing.assert_allclose(L.lens_parameters(out)["grazing_reflectance_rgb"], g, atol=1e-4)
        self.assertEqual(out["normal_reflectance_rgb"], MIRROR["normal_reflectance_rgb"])
        kinds = {n["kind"]: n for n in notes}
        self.assertEqual(set(kinds), {"angular_remapped", "knots_changed"})
        self.assertEqual((kinds["knots_changed"]["knots_before"], kinds["knots_changed"]["knots_after"]), (4, 5))
        self.assertFalse(any(n["changes_request"] for n in notes))
        # restating the edited lens is a no-op again
        again, notes = L.compile_lens(dict(operation="lens_look", lens="all", **L.lens_parameters(out)), out)
        self.assertEqual((again, notes), (out, []))
        # the angular summary reports the knot change against S9
        self.assertEqual(L.angular_summary(out, MIRROR)["table_knots"], 5)
        self.assertTrue(L.angular_summary(out, MIRROR)["knots_changed_vs_s9"])
        self.assertIn("Schlick", L.angular_summary(UNIFORM, UNIFORM)["source"])

    def test_a_small_grazing_edit_never_amplifies_a_measured_table(self):
        """The 2026-09-25 review: a +0.05 blue grazing edit on INVU turned its 20-55 deg blue reflectance from
        0.22-0.36 into 0.98-1.0 (a mirror in the front view); a green 0.40 on OAKLEY moved 17-28 deg from 0.90 to
        0.99. Now no knot moves by more than the edit, and 70 deg lands exactly on it."""
        for base, channel, value in ((INVU, 2, None), (OAKLEY, 1, 0.40), (INVU, 0, None)):
            with self.subTest(channel="RGB"[channel]):
                p = L.lens_parameters(base)
                g = list(p["grazing_reflectance_rgb"])
                g[channel] = round(g[channel] + 0.05, 4) if value is None else value
                out, notes = L.compile_lens(dict(operation="lens_look", lens="all", **{**p, "grazing_reflectance_rgb": g}), base)
                self.check(out)
                old, new = table(base), table(out)
                step = abs(g[channel] - p["grazing_reflectance_rgb"][channel])
                for a in old:
                    self.assertLessEqual(float(np.abs(new[a] - old[a]).max()), step + 1e-6, f"{a} deg")
                np.testing.assert_allclose(new[70.0], g, atol=1e-6)
                self.assertEqual(new[90.0].tolist(), [1.0, 1.0, 1.0])
                self.assertFalse(any(n["kind"] in ("angular_clipped", "energy_cap") for n in notes))
                # the front view (INVU's median incidence 33 deg) keeps what it looked like
                Y = lambda d: float(np.asarray(LensAppearance.from_dict(d).evaluate(0.5, 33.0).transmission_rgb) @ L.LUMA)  # noqa: E731
                self.assertLess(abs(Y(out) - Y(base)), 0.01)

    def test_a_new_head_on_reflectance_moves_a_measured_table_inside_0_1_with_its_shape(self):
        for base, r0 in ((INVU, (0.2, 0.2, 0.2)), (INVU, (0.75, 0.65, 0.55)), (OAKLEY, (0.05, 0.3, 0.1))):
            with self.subTest(base=base["normal_reflectance_rgb"], r0=r0):
                p = L.lens_parameters(base)
                out, notes = L.compile_lens(dict(operation="lens_look", lens="all", **{**p, "normal_reflectance_rgb": list(r0)}), base)
                self.check(out)
                self.assertEqual([n["kind"] for n in notes], ["angular_rescaled"])
                old, new = table(base), table(out)
                R0o = np.asarray(base["normal_reflectance_rgb"])
                for a in old:
                    if 0 < a < 90:     # every dip stays a dip and every rise a rise (the order of the values is kept)
                        self.assertTrue(np.all(np.sign(np.round(new[a] - r0, 9)) == np.sign(np.round(old[a] - R0o, 9))), a)
                    self.assertTrue(np.all((new[a] >= 0) & (new[a] <= 1)))
                # only the mirror moved: what is seen through the lens is kept exactly at every knot
                np.testing.assert_allclose(knot_T(out), knot_T(base), atol=2e-6)

    def test_see_through_covers_oblique_incidence(self):
        bright_20_30 = _desc([(0.0, (0.3, 0.3, 0.3))], r0=(0.04, 0.04, 0.04), angular=[
            {"angle_degrees": 0.0, "reflectance_rgb": [0.04] * 3}, {"angle_degrees": 25.0, "reflectance_rgb": [0.985] * 3},
            {"angle_degrees": 50.0, "reflectance_rgb": [0.1] * 3}, {"angle_degrees": 90.0, "reflectance_rgb": [1.0] * 3}])
        st = L.see_through(bright_20_30)
        self.assertGreater(st["min_head_on"], 0.5)                   # head-on it is a clear lens ...
        self.assertFalse(st["ok"])                                   # ... but opaque at 25 deg
        self.assertEqual(st["at_angle_deg"], 25.0)
        p = L.lens_parameters(bright_20_30)
        with self.assertRaisesRegex(ValueError, "25 deg incidence"):
            L.compile_lens(dict(operation="lens_look", lens="all", **{**p, "roughness": 0.2}), bright_20_30)
        # an export already darker than the floor is not refused for what it was (the floor follows S9)
        floor = L.see_through_floor({"lens_R": bright_20_30})
        out, _ = L.compile_lens(dict(operation="lens_look", lens="all", **{**p, "roughness": 0.2}), bright_20_30, floor=floor)
        self.assertEqual(out["roughness"], 0.2)
        # every m2-like measured lens passes over 0-60 deg
        for d in (INVU, OAKLEY, GRADIENT, MIRROR):
            self.assertTrue(L.see_through(d)["ok"])

    def test_gradient_edits_keep_a_non_monotone_profile_without_amplifying_it(self):
        """OAKLEY's gentle gradient edit gave an interior red transmission of 0.51 (asked 0.22-0.30) and green 0.023
        (asked 0.06-0.075); a larger edit reported an energy cap whose requested and applied values were equal."""
        p = L.lens_parameters(OAKLEY)
        req = dict(operation="lens_look", lens="all", **{**p, "transmission_rgb_top": [0.22, 0.06, 0.12],
                                                       "transmission_rgb_bottom": [0.30, 0.075, 0.15]})
        out, notes = L.compile_lens(req, OAKLEY)
        self.assertEqual(notes, [])
        la_old, la_new = LensAppearance.from_dict(OAKLEY), LensAppearance.from_dict(out)
        v = np.asarray([k.v for k in la_old.optical_density_keyframes])
        D_old = -np.log(knot_T(OAKLEY))
        D_new = -np.log(knot_T(out))
        line = lambda D: D[0] + (D[-1] - D[0]) * v[:, None]  # noqa: E731
        # no channel deviates from the straight line between its ends by more than the measured profile did
        self.assertTrue(np.all(np.abs(D_new - line(D_new)).max(0) <= np.abs(D_old - line(D_old)).max(0) + 1e-5))
        np.testing.assert_allclose(L.lens_parameters(out)["transmission_rgb_top"], [0.22, 0.06, 0.12], atol=1e-4)
        np.testing.assert_allclose(L.lens_parameters(out)["transmission_rgb_bottom"], [0.30, 0.075, 0.15], atol=1e-4)
        T = knot_T(out)
        self.assertLess(T[:, 0].max(), 0.32)
        self.assertGreater(T[:, 1].min(), 0.055)
        # ends within 1 - R0: no energy cap, whatever the interior does
        big = dict(req, transmission_rgb_top=[0.2, 0.2, 0.2], transmission_rgb_bottom=[0.4, 0.35, 0.3])
        _, notes = L.compile_lens(big, OAKLEY)
        self.assertFalse(any(n["kind"] == "energy_cap" for n in notes))
        # INVU's red is lighter inside than at its ends: the ends are applied exactly and the interior clip is
        # reported as what it is (interior_clipped), never as an energy cap of the request
        pi = L.lens_parameters(INVU)
        out, notes = L.compile_lens(dict(operation="lens_look", lens="all", **{**pi, "transmission_rgb_top": [0.2, 0.2, 0.2],
                                                                             "transmission_rgb_bottom": [0.4, 0.35, 0.3]}), INVU)
        self.assertEqual([n["kind"] for n in notes], ["interior_clipped"])
        self.assertFalse(notes[0]["changes_request"])
        self.assertTrue(all(c == "R" for _, c in notes[0]["knots"]))
        np.testing.assert_allclose(L.lens_parameters(out)["transmission_rgb_bottom"], [0.4, 0.35, 0.3], atol=1e-4)
        self.assertGreater(knot_T(out)[:, 2].min(), 0.17)       # blue no longer dips to 0.093

    def test_lens_caption_names_a_measured_table(self):
        lv = {"lens_node": "lens_R", "incidence_deg": {"median": 33.0, "p25": 26.0, "p75": 45.0},
              "at_median_incidence": {"reflectance_rgb": [0.28, 0.17, 0.27], "transmission_rgb_mid_height": [0.2, 0.3, 0.4]},
              "head_on_reflectance_rgb": [0.51, 0.48, 0.58], "angle_table": True}
        cap = L.lens_caption("front", "front", lv)
        self.assertIn("MEASURED angle table", cap)
        self.assertIn("[0.28, 0.17, 0.27] against [0.51, 0.48, 0.58] head-on", cap)
        self.assertNotIn("Here the colour is set by", cap)
        self.assertIn("Here the colour is set by", L.lens_caption("front", "front", {**lv, "angle_table": False}))

    def test_energy_cap_and_opaque_refusal(self):
        out, notes = L.compile_lens(lens_op(top=(0.6, 0.45, 0.6), bottom=(0.6, 0.3, 0.6), r0=(0.6, 0.5, 0.6)), UNIFORM)
        cap = notes[0]
        self.assertEqual((cap["kind"], cap["changes_request"], cap["channels"]), ("energy_cap", True, ["R", "B"]))
        self.assertEqual(cap["requested"]["transmission_rgb_top"], [0.6, 0.45, 0.6])
        np.testing.assert_allclose(cap["applied"]["transmission_rgb_top"], [0.4, 0.45, 0.4], atol=1e-4)
        np.testing.assert_allclose(cap["applied"]["transmission_rgb_bottom"], [0.4, 0.3, 0.4], atol=1e-4)
        np.testing.assert_allclose(cap["limit_rgb"], [0.4, 0.5, 0.4], atol=1e-6)
        self.assertIn("capped", cap["note"])
        np.testing.assert_allclose(L.lens_parameters(out)["transmission_rgb_top"], [0.4, 0.45, 0.4], atol=1e-4)
        with self.assertRaisesRegex(ValueError, "nearly opaque"):
            L.compile_lens(lens_op(top=(0.02, 0.02, 0.02), bottom=(0.02, 0.02, 0.02)), UNIFORM)
        with self.assertRaisesRegex(ValueError, "nearly opaque"):
            L.compile_lens(lens_op(top=(0.01, 0.01, 0.01), bottom=(0.5, 0.5, 0.5)), GRADIENT)


class PixelLabelTest(unittest.TestCase):
    @staticmethod
    def quad(z, node, material, half=0.02):
        V = np.array([[-half, -half, z], [half, -half, z], [half, half, z], [-half, half, z]], float)
        return {"node": node, "material": material, "V": V, "F": np.array([[0, 1, 2], [0, 2, 3]])}

    def test_label_views_sees_through_the_lens_and_measures_incidence(self):
        prims = [self.quad(0.0, "lens_L", "lens"), self.quad(-0.01, "frame", "frame_front", 0.01)]
        cams = {"front": camera([0, 0, 0.4]), "oblique": camera([0.3, 0, 0.3])}
        lab = L.label_views(prims, cams, {0})
        f = lab["front"]
        self.assertEqual((f["first"][240, 360], f["behind"][240, 360]), (0, 1))     # frame seen through the lens
        edge = np.nonzero(f["first"][240] == 0)[0]
        self.assertEqual((f["first"][240, edge[0]], f["behind"][240, edge[0]]), (0, -1))   # backdrop behind the lens
        self.assertAlmostEqual(float(f["cos"][240, 360]), 1.0, places=3)
        self.assertEqual(f["first"][5, 5], -1)
        o = lab["oblique"]
        ang = np.degrees(np.arccos(np.nanmedian(o["cos"][o["first"] == 0])))
        self.assertAlmostEqual(float(ang), 45.0, delta=3.0)
        # the map: flat material colours, the lens striped with what lies behind it
        colours = L.material_colours(["lens", "frame_front"])
        img = np.asarray(L.paint_material_map(f, prims, colours, {0}))
        centre = img[230:250, 350:370].reshape(-1, 3)
        self.assertTrue({tuple(c) for c in centre} >= {colours["lens"], colours["frame_front"]})
        self.assertEqual(tuple(img[5, 5]), L.WHITE)

    def test_fit_view_names_follow_the_render_not_a_yaw_rule(self):
        self.assertEqual(L.fit_view_name("left", "side_neg", [0.2, 0, 0]),
                         "left side: view 'side_neg', yaw -80 deg, camera at +X (wearer's left, the *_R parts)")
        self.assertIn("yaw +80 deg, camera at -X", L.fit_view_name("right", "side_pos", [-0.2, 0, 0]))
        self.assertIn("camera in front", L.fit_view_name("front", "front", [0, 0, 0.2]))

    def test_tryon_views_are_cropped_to_the_glasses_and_wide(self):
        with tempfile.TemporaryDirectory() as d:
            paths = []
            for i, box in enumerate(([300, 220, 420, 260], [280, 200, 460, 280], [330, 210, 390, 270])):
                im = Image.fromarray(archeck.checker_background())
                ImageDraw.Draw(im).rectangle(box, fill=(30, 30, 90))
                paths.append(str(Path(d) / f"v{i}.png"))
                im.save(paths[-1])
            out = L.tryon_sheet([("current r0001", paths)], Path(d) / "sheet.png", title="t")
            a = np.asarray(Image.open(out).convert("RGB")).astype(int)
            self.assertGreaterEqual(a.shape[1], 3 * L.TRYON_MIN_WIDTH)
            # each column: a crop around its glasses (not the 720 px frame), scaled to >= 640 px
            self.assertLess(a.shape[1], 3 * 720 * 1.5)
            dark = (a.sum(-1) < 200)
            cols = np.nonzero(dark.any(0))[0]
            self.assertGreater(cols.max() - cols.min(), 2 * L.TRYON_MIN_WIDTH)


class SessionTest(LookFixture):
    def test_inputs_never_include_the_held_out_photo(self):
        inputs = L.prepare_inputs(RUN, PROD, self.root / "inputs")
        self.assertEqual(sorted(inputs["photos"]), sorted(core.FIT_VIEWS))
        self.assertEqual(inputs["held_out_excluded"], ["angled"])
        self.assertFalse(list((self.root / "inputs").rglob("angled*")))
        self.assertEqual(inputs["s10"]["decision"], "READY")

    def test_driver_none_is_the_unedited_baseline(self):
        res, _ = self.main("--driver", "none")
        self.assertEqual((res["status"], res["verdict"], res["final_revision"]), ("delivered", "not_edited", "r0000"))
        self.assertEqual((self.sd / "model.glb").read_bytes(), self.s9)
        # 4 fit views + material map + lens sheet + try-on
        self.assertEqual(sorted(p.stem for p in (self.sd / "sheets").glob("*.png")), sorted(IMAGE_IDS))
        self.assertTrue(res["geometry_identical"] and res["contract_ok"] and res["ar_runtime_compatible"])
        self.assertEqual(res["final_decision"], "READY")
        self.assertIsNone(res["finish"])

    def test_every_image_names_its_view_the_same_way(self):
        self.main("--driver", "none")
        obs = self.observation()
        self.assertEqual(obs["sheet_errors"], [])
        self.assertEqual([s["id"] for s in obs["sheets"]], IMAGE_IDS)
        fv = obs["fit_views"]
        self.assertEqual((fv["left"]["render_view"], fv["right"]["render_view"]), ("side_neg", "side_pos"))
        self.assertIn("yaw -80 deg, camera at +X", fv["left"]["name"])
        for pv in core.FIT_VIEWS:
            label = next(s["label"] for s in obs["sheets"] if s["id"] == f"fit-{pv}")
            self.assertIn(fv[pv]["name"], label)
        with L._job_lock(self.sd / "session"):
            ctx, images = L.BsaLookSession(self.sd / "session").snapshot()
        self.assertEqual({pv: v["name"] for pv, v in ctx["views"].items()}, {pv: v["name"] for pv, v in fv.items()})
        self.assertEqual([i["label"] for i in images], [i["shows"] for i in ctx["images"]])
        mm = next(s["label"] for s in obs["sheets"] if s["id"] == "material-map")
        self.assertIn(fv["front"]["name"], mm)
        self.assertIn(fv["left"]["name"], mm)

    def test_material_map_paints_each_material_and_explains_it(self):
        self.main("--driver", "none")
        obs = self.observation()
        legend = {r["material"]: r for r in obs["material_map"]["legend"]}
        self.assertEqual(set(legend), {"frame", "temple", "lens"})
        self.assertEqual((legend["frame"]["role"], legend["lens"]["role"]), ("frame_material target", "lens_look target"))
        self.assertEqual(legend["temple"]["nodes"], ["temple_L", "temple_R"])
        a = np.asarray(Image.open(self.sd / "sheets" / "material-map.png").convert("RGB"))
        for m in ("frame", "temple"):
            hits = (a == np.array(legend[m]["rgb"], np.uint8)).all(-1).sum()
            self.assertGreater(int(hits), 200, m)        # the swatch alone is ~400 px: the map itself paints it too
        # the painted map from the same labels: the bridge is frame, the lens centre is (pale) lens
        meta = camera(CAMS["front"])
        prims = texture.glb_primitives(self.s9)
        by_node, _ = L._glb_lens_info(self.s9, prims)
        lens_prims = {i for ids in by_node.values() for i in ids}
        lab = L.label_views(prims, {"front": meta}, lens_prims)["front"]
        uv = texture.ar_project(meta, np.array([[0.0, 0.015, 0.001], [0.032, 0.002, 0.0]])).astype(int)
        self.assertEqual(prims[lab["first"][uv[0, 1], uv[0, 0]]]["material"], "frame")
        self.assertEqual(prims[lab["first"][uv[1, 1], uv[1, 0]]]["node"], "lens_R")      # +X = lens_R

    def test_lens_sheet_crops_are_large_and_state_the_controlling_parameter(self):
        self.main("--driver", "none")
        obs = self.observation()
        lv = obs["lens_views"]
        self.assertEqual(sorted(lv), ["front", "left", "right"])
        self.assertEqual(lv["front"]["lens_node"], "lens_L")        # the lens on the image's left (-X)
        self.assertLess(lv["front"]["incidence_deg"]["median"], 35)
        for side in ("left", "right"):
            self.assertGreater(lv[side]["incidence_deg"]["median"], 50)
            self.assertEqual(len(lv[side]["at_median_incidence"]["reflectance_rgb"]), 3)
        for pv, v in lv.items():                                    # the photo crop lies inside the photo
            x0, y0, x1, y1 = v["photo_crop"]
            self.assertTrue(0 <= x0 < x1 <= 640 and 0 <= y0 < y1 <= 360, v["photo_crop"])
        with Image.open(self.sd / "sheets" / "lens.png") as im:
            im.load()
        self.assertEqual(im.width, 2 * L.LENS_TILE[0] + 10)
        self.assertGreaterEqual(min(L.LENS_TILE), 500)
        self.assertGreater(im.height, 3 * L.LENS_TILE[1])           # three rows of >= 500 px tiles
        label = next(s["label"] for s in obs["sheets"] if s["id"] == "lens")
        self.assertIn("grazing_reflectance_rgb, the reflectance at 70 deg", label)
        cap = L.lens_caption("left", "left side", lv["left"])
        self.assertIn("grazing_reflectance_rgb (the 70-deg value) dominates", cap)
        self.assertIn("normal_reflectance_rgb", L.lens_caption("front", "front", lv["front"]))
        self.assert_no_held_out([self.sd / "sheets" / "lens.png", self.sd / "sheets" / "material-map.png"])

    def test_a_sheet_failure_is_reported_not_hidden(self):
        FakeHarness.no_cameras = True
        res, _ = self.main("--driver", "none")
        self.assertTrue(res["ar_runtime_compatible"])                # the revision itself is fine
        obs = self.observation()
        self.assertEqual([s["id"] for s in obs["sheets"]], ["fit-front", "fit-back", "fit-left", "fit-right", "tryon-current"])
        self.assertTrue(any("no camera" in e for e in obs["sheet_errors"]))
        with L._job_lock(self.sd / "session"):
            ctx, _ = L.BsaLookSession(self.sd / "session").snapshot()
        self.assertEqual(ctx["image_problems"], obs["sheet_errors"])

    def test_scripted_two_turn_session_end_to_end(self):
        res, out = self.main("--driver", "scripted", "--script", str(self.script(
            plan(frame_op(roughness=0.3), note="acetate reads glossier in every photo"), FINISH)))
        self.assertEqual((res["status"], res["verdict"], res["final_revision"]), ("delivered", "improved", "r0001"))
        self.assertEqual([r["id"] for r in res["revisions"]], ["r0000", "r0001"])
        self.assertEqual([t["status"] for t in res["turns"]], ["applied", "applied"])
        self.assertEqual([t["error_type"] for t in res["turns"]], [None, None])
        self.assertEqual((res["paid_calls_used"], res["model_reviewed_current"]), (0, False))
        self.assertEqual(res["final_decision"], "READY")
        self.assertTrue(res["geometry_identical"] and res["contract_ok"] and res["ar_runtime_compatible"])
        glb = (self.sd / "model.glb").read_bytes()
        self.assertNotEqual(glb, self.s9)
        self.assertEqual(texture.glb_split(glb)[1], texture.glb_split(self.s9)[1])
        doc = texture.glb_split(glb)[0]
        self.assertEqual({m["name"]: m["pbrMetallicRoughness"].get("roughnessFactor") for m in doc["materials"]},
                         {"frame": 0.3, "temple": 0.3, "lens": 0.05})
        lk = json.loads((self.sd / "look.json").read_text())
        self.assertEqual(lk["input_glb"]["sha256"], hashlib.sha256(self.s9).hexdigest())
        self.assertEqual(lk["output_glb_sha256"], hashlib.sha256(glb).hexdigest())
        self.assertTrue(lk["checks"]["geometry"]["ok"])
        self.assertEqual(len(res["sheets"]), len(IMAGE_IDS) + 1)                     # + baseline vs current
        sent = self.sent_images()
        self.assertEqual(len(sent), len(IMAGE_IDS) + len(IMAGE_IDS) + 1)             # turn 0: 7 images, turn 1: 8
        self.assert_no_held_out(sent)
        ctx = self.turn_input(1)["context"]
        text = json.dumps(ctx)
        self.assertNotIn("fixture kind text", text)                                  # never the hand-typed kind
        self.assertNotIn(str(self.root), text)                                       # no local paths
        for key in ("product_id", "current_revision", "last_turn", "revisions", "views", "materials", "lens",
                    "s10_gate", "previous_events", "images", "lens_views", "lighting_note",
                    "turns_remaining_including_this", "limits"):
            self.assertIn(key, ctx)
        self.assertEqual(ctx["last_turn"]["status"], "applied")
        self.assertIn("APPLIED -> r0001", ctx["last_turn"]["headline"])
        cur = ctx["lens"]["current"]["all"]
        self.assertEqual(cur["parameters"], L.lens_parameters(GRADIENT))
        self.assertIsNotNone(cur["parameters"]["grazing_reflectance_rgb"])
        self.assertEqual(cur["parameters"]["grazing_reflectance_rgb"], cur["reflectance_at_70deg_rgb"])
        self.assertEqual([m["roughness"] for m in ctx["materials"]], [0.3, 0.3])
        self.assertEqual(json.loads(out.strip().splitlines()[-1])["verdict"], "improved")
        with Image.open(self.sd / "sheets" / "tryon-baseline-vs-current.png") as tryon:
            self.assertGreaterEqual(tryon.width, 3 * L.TRYON_MIN_WIDTH)

    def test_images_and_context_stay_within_budget(self):
        self.main("--driver", "scripted", "--script", str(self.script(
            plan(frame_op(ratio=[0.9, 0.9, 0.9])), plan(lens_op(top=(0.15, 0.15, 0.15), bottom=(0.5, 0.45, 0.4))),
            FINISH)))
        for n in range(3):
            snap = self.turn_input(n)
            self.assertLessEqual(len(snap["images"]), L.MAX_IMAGES)
            self.assertLess(len(json.dumps(snap["context"]).encode()), 1024 * 1024)

    def test_energy_cap_is_reported_in_the_same_turn_and_first_in_the_next_context(self):
        res, _ = self.main("--driver", "scripted", "--script", str(self.script(
            plan(lens_op(top=(0.9, 0.3, 0.3), bottom=(0.9, 0.3, 0.3), r0=(0.3, 0.3, 0.3)), note="brighter mirror"),
            FINISH)))
        t0 = res["turns"][0]
        self.assertEqual((t0["status"], t0["event_status"]), ("applied", "applied_with_adjustment"))
        self.assertTrue(any("capped" in a for a in t0["adjusted"]))
        event = json.loads(next((self.sd / "session" / "edits" / "turn-0000").glob("a*/event.json")).read_text())
        self.assertEqual(event["status"], "applied_with_adjustment")      # this turn's own event, not only the next one's
        cap = next(a for a in event["adjustments"] if a["kind"] == "energy_cap")
        self.assertEqual((cap["channels"], cap["requested"]["transmission_rgb_top"]), (["R"], [0.9, 0.3, 0.3]))
        np.testing.assert_allclose(cap["applied"]["transmission_rgb_top"], [0.7, 0.3, 0.3], atol=1e-4)
        ctx = self.turn_input(1)["context"]
        self.assertEqual(list(ctx).index("last_turn"), list(ctx).index("current_revision") + 1)
        lt = ctx["last_turn"]
        self.assertEqual(lt["status"], "applied_with_adjustment")
        self.assertIn("APPLIED WITH ADJUSTMENT", lt["headline"])
        self.assertEqual(lt["adjusted"][0]["requested"]["transmission_rgb_top"], [0.9, 0.3, 0.3])
        self.assertEqual(ctx["limits"]["lens_energy"], L.ENERGY_RULE)
        self.assertEqual(ctx["lens"]["energy_rule"], L.ENERGY_RULE)

    def test_lens_state_after_an_edit_is_consistent_and_reports_knot_changes(self):
        self.main("--driver", "scripted", "--script", str(self.script(
            plan(lens_op(top=(0.3, 0.3, 0.3), bottom=(0.3, 0.3, 0.3), r0=(0.1, 0.1, 0.1), grazing=(0.3, 0.3, 0.4))),
            FINISH)))
        ctx = self.turn_input(1)["context"]
        cur = ctx["lens"]["current"]["all"]
        np.testing.assert_allclose(cur["parameters"]["grazing_reflectance_rgb"], [0.3, 0.3, 0.4], atol=1e-4)
        self.assertEqual(cur["parameters"]["grazing_reflectance_rgb"], cur["reflectance_at_70deg_rgb"])
        self.assertEqual((cur["angular"]["table_knots"], cur["angular"]["s9_table_knots"]), (6, 0))
        self.assertTrue(cur["angular"]["knots_changed_vs_s9"])
        self.assertEqual(cur["density_knots"], {"now": 1, "s9": 3})
        knots = ctx["last_turn"]["knots_changed"]
        self.assertEqual(len(knots), 2)
        self.assertTrue(any("density knots 3 -> 1" in k for k in knots))
        self.assertTrue(any("angular table 0 -> 6" in k for k in knots))
        self.assertEqual(ctx["last_turn"]["status"], "applied")          # a knot change is reported, not an adjustment

    def test_finish_can_deliver_an_earlier_revision_in_one_step(self):
        res, _ = self.main("--driver", "scripted", "--script", str(self.script(
            plan(frame_op(ratio=[0.9, 0.9, 0.9])), plan(frame_op(roughness=0.2)),
            finish("best_effort", "r0002's gloss is wrong; the first edit was closer", deliver="r0009"),
            finish("best_effort", "r0002's gloss is wrong; the first edit was closer", deliver="r0001"))))
        self.assertEqual((res["verdict"], res["final_revision"]), ("best_effort", "r0001"))
        self.assertEqual([t["event_status"] for t in res["turns"]], ["applied", "applied", "rejected", "applied"])
        self.assertIn("deliver_revision r0009 is not a committed revision", res["turns"][2]["event_error"])
        self.assertEqual(res["turns"][3]["delivered_revision"], "r0001")
        self.assertEqual(res["finish"]["deliver_revision"], "r0001")
        r1 = next(r for r in res["revisions"] if r["id"] == "r0001")
        self.assertEqual(hashlib.sha256((self.sd / "model.glb").read_bytes()).hexdigest(), r1["glb_sha256"])

    def test_the_turn_limit_keeps_the_plan_notes_as_the_finish(self):
        res, _ = self.main("--driver", "scripted", "--max-turns", "2", "--script", str(self.script(
            plan(frame_op(ratio=[0.9, 0.9, 0.9]), note="darker frame"),
            plan({"operation": "restore", "revision": "r0000"}, note="the cyan stem is geometry, not colour"))))
        self.assertEqual((res["verdict"], res["final_revision"]), ("unfinished_turn_limit", "r0000"))
        f = res["finish"]
        self.assertEqual((f["verdict"], f["recorded_by"], f["stop_reason"]), (None, "host", "turn_limit"))
        self.assertEqual(f["note"], "the cyan stem is geometry, not colour")
        self.assertEqual([n["note"] for n in f["turn_notes"]], ["darker frame", "the cyan stem is geometry, not colour"])

    def test_rejection_is_recorded_and_restore_returns_exact_bytes(self):
        res, _ = self.main("--driver", "scripted", "--script", str(self.script(
            plan(frame_op("frame", ratio=[1.1, 1.0, 0.9])),
            plan(lens_op(top=(0.02, 0.02, 0.02), bottom=(0.02, 0.02, 0.02))),
            plan({"operation": "restore", "revision": "r0000"}),
            finish("best_effort", "the export was closer"))))
        self.assertEqual((res["verdict"], res["final_revision"]), ("best_effort", "r0000"))
        self.assertEqual([t["event_status"] for t in res["turns"]], ["applied", "rejected", "applied", "applied"])
        self.assertIn("nearly opaque", res["turns"][1]["event_error"])
        self.assertEqual((self.sd / "model.glb").read_bytes(), self.s9)
        ctx = self.turn_input(2)["context"]
        self.assertIn("nearly opaque", ctx["previous_events"][-1]["error"])
        self.assertEqual(ctx["last_turn"]["status"], "rejected")
        self.assertIn("REJECTED", ctx["last_turn"]["headline"])
        self.assertIn("nearly opaque", ctx["last_turn"]["headline"])
        self.assertEqual(ctx["current_revision"], "r0001")

    def test_a_failed_observation_never_commits_the_revision(self):
        FakeHarness.fail_tryon_after = 1                   # r0000 observes; the edit's observation is rejected
        res, _ = self.main("--driver", "scripted", "--script", str(self.script(plan(frame_op(roughness=0.4)), FINISH)))
        self.assertEqual(res["final_revision"], "r0000")
        self.assertEqual([r["id"] for r in res["revisions"]], ["r0000"])
        self.assertIn("AR observation did not validate", res["turns"][0]["event_error"])
        self.assertEqual((self.sd / "model.glb").read_bytes(), self.s9)

    def test_manual_driver_writes_a_package_then_applies_the_plan(self):
        plans = self.root / "plans"
        res, out = self.main("--driver", "manual", "--plans-dir", str(plans))
        self.assertEqual(res["status"], "awaiting_plan")
        self.assertIn(f"awaiting {plans / 'turn-0000.json'}", out)
        pkg = plans / "turn-0000"
        for name in ("instructions.txt", "context.json", "schema.json", "images.json"):
            self.assertTrue((pkg / name).exists(), name)
        rows = json.loads((pkg / "images.json").read_text())
        self.assertEqual([r["image_id"] for r in rows], IMAGE_IDS)
        ctx = json.loads((pkg / "context.json").read_text())
        self.assertEqual([r["label"] for r in rows], [i["shows"] for i in ctx["images"]])   # one label everywhere
        self.assert_no_held_out([pkg / r["file"] for r in rows])
        self.assertTrue((pkg / "instructions.txt").read_text().startswith(L.LOOK_PROMPT))
        self.assertEqual(json.loads((pkg / "schema.json").read_text()), L.build_tools_schema(L.glb_inventory(self.s9)))
        state = json.loads((self.sd / "session" / "state.json").read_text())
        self.assertEqual(state["turns"][0]["status"], "awaiting_plan")
        self.assertNotIn("error_type", state["turns"][0])
        self.assertEqual(json.loads((self.sd / "session" / "report.json").read_text())["status"], "awaiting_plan")
        (plans / "turn-0000.json").write_text(json.dumps(plan(lens_op(top=(0.15, 0.15, 0.15), bottom=(0.5, 0.45, 0.4)))))
        res, out = self.main("--driver", "manual", "--plans-dir", str(plans))
        self.assertEqual((res["status"], res["final_revision"]), ("awaiting_plan", "r0001"))
        self.assertIn("turn-0001.json", out)
        self.assertNotIn("needs_attention", out)                     # the applied turn never reads needs_attention
        self.assertEqual((res["turns"][0]["status"], res["turns"][0]["error_type"], res["turns"][0]["error"]),
                         ("applied", None, None))
        self.assertEqual(res["turns"][0]["event_status"], "applied")
        self.assertEqual(res["turns"][1]["status"], "awaiting_plan")
        state = json.loads((self.sd / "session" / "state.json").read_text())
        self.assertFalse({"error_type", "error", "awaiting"} & set(state["turns"][0]))
        self.assertEqual(len(json.loads((plans / "turn-0001/images.json").read_text())), len(IMAGE_IDS) + 1)
        (plans / "turn-0001.json").write_text(json.dumps(FINISH))
        res, out = self.main("--driver", "manual", "--plans-dir", str(plans))
        self.assertEqual((res["status"], res["verdict"], res["final_revision"]), ("delivered", "improved", "r0001"))
        self.assertNotIn("needs_attention", out)
        self.assertEqual([(t["status"], t["error_type"]) for t in res["turns"]], [("applied", None), ("applied", None)])
        doc = texture.glb_split((self.sd / "model.glb").read_bytes())[0]
        lens = next(m for m in doc["materials"] if m["name"] == "lens")
        np.testing.assert_allclose(L.lens_parameters(lens["extensions"][L.LENS_APPEARANCE_EXTENSION]["appearance"])
                                   ["transmission_rgb_top"], [0.15] * 3, atol=1e-4)

    def test_changed_code_or_an_edited_session_refuses_to_reopen(self):
        self.main("--driver", "scripted", "--script", str(self.script(plan(frame_op(metallic=0.2)), FINISH)))
        with self.assertRaisesRegex(ValueError, "--driver none needs --fresh"):
            self.main("--driver", "none")
        self.assertEqual(json.loads((self.sd / "result.json").read_text())["status"], "failed")
        changed = L.implementation()
        changed["files"]["bsa/look.py"] = "0" * 64
        with mock.patch.object(L, "_IMPLEMENTATION", changed), self.assertRaisesRegex(ValueError, "implementation changed"):
            L.BsaLookSession(self.sd / "session")
        res, _ = self.main("--driver", "none", "--fresh")             # the edited session is set aside, not deleted
        self.assertEqual(res["verdict"], "not_edited")
        aside = list(self.sd.parent.glob(f"{L.STAGE}.superseded-*"))
        self.assertEqual(len(aside), 1)
        self.assertTrue((aside[0] / "session" / "state.json").exists())

    def test_implementation_pins_the_shared_helpers_and_one_runtime_digest(self):
        fns = L.implementation()["functions"]
        for key in ("texture.glb_split", "reconstruction.job._write", "reconstruction.job._verify",
                    "reconstruction.segmented_astra_session.digest", "reconstruction.segmented_providers.verified",
                    "reconstruction.atomic_files.replace_with_retry", "qa.provider_benchmark.digest"):
            self.assertIn(key, fns)
        self.assertTrue({"bsa/export.py", "bsa/core.py"} <= set(L.implementation()["files"]))
        # the observation's renderer digest is the pipeline's ar_runtime fingerprint (one list of files)
        self.assertEqual(L.ar_runtime_digest(), pipeline.ar_runtime_digest())

    def test_cli_refuses_paid_flags_unauthorized_astra_and_the_real_m1(self):
        script = self.script(FINISH)
        for argv in (["--driver", "scripted", "--script", str(script), "--authorize-paid-astra"],
                     ["--driver", "astra", "--astra-budget", str(self.root / "b.json"), "--astra-maximum-calls", "1"],
                     ["--driver", "astra", "--authorize-paid-astra", "--astra-budget", str(self.root / "b.json")],
                     ["--driver", "none", "--astra-script", str(script)]):
            with self.subTest(argv=argv), self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                self.main(*argv)
        with mock.patch.object(core, "BSA_DATA", core.DATA / "bsa"), self.assertRaises(SystemExit), \
                contextlib.redirect_stderr(io.StringIO()):
            L.main(["--run", "m1", "--product", "rayban", "--driver", "none"])
        self.assertFalse(self.sd.exists())
        self.assertFalse((core.DATA / "bsa" / "runs" / "m1" / "rayban" / L.STAGE).exists())


    def test_a_last_turn_edit_is_never_delivered_unseen_and_a_no_op_is_rejected(self):
        res, _ = self.main("--driver", "scripted", "--max-turns", "3", "--script", str(self.script(
            plan(frame_op(ratio=[1.0, 1.0, 1.0]), note="restating the frame"),
            plan(frame_op(roughness=0.3), note="glossier"), plan(frame_op(roughness=0.2), note="glossier still"))))
        self.assertEqual([t["event_status"] for t in res["turns"]], ["rejected", "applied", "applied"])
        self.assertIn("unchanged", res["turns"][0]["event_error"])            # a no-op renders nothing
        self.assertEqual([r["id"] for r in res["revisions"]], ["r0000", "r0001", "r0002"])
        # r0002 was made on the last turn and never shown to the editor: the last reviewed revision is delivered
        self.assertEqual((res["final_revision"], res["unreviewed_revision"], res["verdict"]),
                         ("r0001", "r0002", "unfinished_turn_limit"))
        self.assertIn("look_final_edit_unreviewed", res["flags"])
        self.assertEqual(res["finish"]["delivered_revision"], "r0001")
        r1 = next(r for r in res["revisions"] if r["id"] == "r0001")
        self.assertEqual(hashlib.sha256((self.sd / "model.glb").read_bytes()).hexdigest(), r1["glb_sha256"])
        # the held-out verdict never reaches the editor; the record keeps it
        for n in range(3):
            gate = self.turn_input(n)["context"]["s10_gate"]
            self.assertEqual((gate["reasons"], gate["flags"]), (["info:front_low_resolution"], []))
        self.assertIn(HELD_OUT_REASON, res["s10_decision"]["reasons"])

    def test_a_reused_session_refuses_changed_upstream_results(self):
        L.prepare_inputs(RUN, PROD, self.root / "inputs")
        L.prepare_inputs(RUN, PROD, self.root / "inputs")                       # unchanged: reused as it is
        rd = core.run_dir(RUN, PROD)
        for rel in ("s8_lens/result.json", "s0_intake/result.json", "s10_gate/result.json"):
            with self.subTest(result=rel):
                path = rd / rel
                before = path.read_bytes()
                path.write_text(json.dumps({**json.loads(before), "rewritten": True}))
                with self.assertRaisesRegex(ValueError, f"{rel} changed since this look session began"):
                    L.prepare_inputs(RUN, PROD, self.root / "inputs")
                path.write_bytes(before)

    def test_a_manual_plan_written_for_another_session_is_refused(self):
        plans = self.root / "plans"
        self.main("--driver", "manual", "--plans-dir", str(plans))              # session 1 writes its package
        (plans / "turn-0000.json").write_text(json.dumps(plan(frame_op(roughness=0.3))))
        res, _ = self.main("--driver", "manual", "--plans-dir", str(plans), "--fresh")   # session 2, same plans dir
        self.assertEqual((res["final_revision"], [r["id"] for r in res["revisions"]]), ("r0000", ["r0000"]))
        self.assertEqual(res["turns"][0]["status"], "needs_attention")
        self.assertIn("different request package", res["turns"][0]["error"])
        (plans / "turn-0000.json").rename(plans / "turn-0000.stale.json")        # moved aside: a fresh package
        res, _ = self.main("--driver", "manual", "--plans-dir", str(plans))
        self.assertEqual(res["status"], "awaiting_plan")
        sid = json.loads((self.sd / "session" / "seed.json").read_text())["session_id"]
        self.assertTrue(json.loads((plans / "turn-0000" / "request.json").read_text())["request_dir"].endswith(f"api-{sid}"))


class AstraTransportTest(LookFixture):
    def test_client_sends_the_look_instructions_and_binds_them(self):
        http = HTTP(result(FINISH))
        client = AstraClient("not-a-real-key", budget_path=self.root / "budget.json", maximum_calls=2, session=http,
                             instructions=L.LOOK_PROMPT)
        default = AstraClient("not-a-real-key", budget_path=self.root / "budget.json", maximum_calls=2, session=http)
        sha = lambda s: hashlib.sha256(s.encode()).hexdigest()  # noqa: E731
        self.assertEqual(client.describe()["instructions_sha256"], sha(L.LOOK_PROMPT))
        # the default (segmented) client describes itself exactly as before the hook: stored driver bindings of
        # existing segmented sessions still match (the prompt stays bound through each request's prompt_sha256)
        self.assertNotIn("instructions_sha256", default.describe())
        self.assertEqual(set(default.describe()), {"protocol", "model", "budget_path", "maximum_calls",
                                                   "maximum_output_tokens", "reasoning_effort", "store",
                                                   "network_policy", "credential"})
        schema = L.build_tools_schema(L.glb_inventory(self.s9))
        image = self.root / "tile.png"
        Image.new("RGB", (8, 8), "#887766").save(image)
        images = [{"id": "fit-front", "label": "fixture", "path": str(image), "sha256": hashlib.sha256(image.read_bytes()).hexdigest()}]
        got = client.decide({"current_revision": "r0000"}, images, self.root / "attempt", tools_schema=schema)
        self.assertEqual(got, FINISH)
        body = json.loads(http.calls[0][1]["data"])
        self.assertEqual(body["instructions"], L.LOOK_PROMPT)
        self.assertEqual(body["tools"][0]["parameters"], schema)
        recipe = json.loads((self.root / "attempt/request.json").read_text())["recipe"]
        self.assertEqual(recipe["prompt_sha256"], sha(L.LOOK_PROMPT))
        with self.assertRaises(ValueError):
            AstraClient("k", budget_path=self.root / "b2.json", maximum_calls=1, instructions="  ")
        for p in (self.root / "attempt").iterdir():
            self.assertNotIn(b"not-a-real-key", p.read_bytes())

    @unittest.skipUnless((core.DATA / "segmented-astra-v1" / "live-miu-001" / "state.json").exists(),
                         "no live segmented sessions (data/ missing)")
    def test_existing_segmented_session_bindings_still_match(self):
        """The instructions hook must not change how a default client describes itself: the live segmented sessions
        (miu, oakley) reopen only when their stored driver binding equals describe()."""
        for name in ("live-miu-001", "live-oakley-001"):
            with self.subTest(session=name):
                stored = json.loads((core.DATA / "segmented-astra-v1" / name / "state.json").read_text())["driver"]["client"]
                client = AstraClient("not-a-real-key", stored["model"], budget_path=Path(stored["budget_path"]),
                                     maximum_calls=stored["maximum_calls"], session=HTTP(result(FINISH)),
                                     maximum_output_tokens=stored["maximum_output_tokens"],
                                     reasoning_effort=stored["reasoning_effort"])
                self.assertEqual(client.describe(), stored)

    def test_run_astra_job_drives_the_look_session_and_its_own_calls_are_counted_by_look(self):
        budget = self.root / "budget.json"
        from reconstruction.segmented_astra_transport import PROTOCOL
        budget.write_text(json.dumps({"protocol": PROTOCOL, "maximum_calls": 3, "reservations": [
            {"ordinal": 1, "request_dir": str(self.root / "another-session/turns/turn-0000/api"),
             "request_sha256": "0" * 64, "reserved_unix": 0}]}))
        http = HTTP(result(FINISH))
        client = AstraClient("not-a-real-key", budget_path=budget, maximum_calls=3, session=http, instructions=L.LOOK_PROMPT)
        sd = core.stage_dir(RUN, PROD, L.STAGE)
        L.prepare_inputs(RUN, PROD, sd.root / "inputs")
        schema = L.build_tools_schema(L.glb_inventory(self.s9))
        with contextlib.redirect_stdout(io.StringIO()):
            report = run_astra_job(sd.root / "inputs", sd.root / "session", client=client, maximum_turns=2,
                                   session_cls=L.BsaLookSession, tools_schema=schema)
        # run_astra_job keeps the segmented semantics (the ledger total); bsa.look counts its own session by its
        # unique request folder (api-<session_id>)
        self.assertEqual((report["stop_reason"], report["paid_calls_used"], report["model_reviewed_current"]),
                         ("model_finished", 2, True))
        self.assertEqual(len(json.loads(budget.read_text())["reservations"]), 2)
        sid = json.loads((sd.root / "session" / "seed.json").read_text())["session_id"]
        self.assertEqual(L.ledger_usage(budget, sid), {"path": str(budget), "maximum_calls": 3, "reservations": 2,
                                                       "this_session": 1})
        self.assertTrue((sd.root / "session" / "turns" / "turn-0000" / f"api-{sid}" / "request.json").exists())
        body = json.loads(http.calls[0][1]["data"])
        images = [c for c in body["input"][0]["content"] if c["type"] == "input_image"]
        self.assertEqual(len(images), len(IMAGE_IDS))
        self.assertLessEqual(len(images), L.MAX_IMAGES)
        self.assertEqual(body["instructions"], L.LOOK_PROMPT)
        self.assert_no_held_out(self.sent_images())


if __name__ == "__main__":
    unittest.main()
