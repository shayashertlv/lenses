"""modeler.agentic.evaluation over a real job Store: sealed reservation, the leak sentinel, critic and final payloads,
the verdict axes, byte-bound delivery and append-only owner verdicts.

Every test builds a fresh job folder under a short temp path (tests/test_agentic_support.py, lag-evaluation) with the
same minimal job record ``runner.Session.create`` writes, and asserts only what is observable: catalogue rows and
settings in the database, files on disk, the blocks a payload would carry, exceptions. The only patched dependency
is ``modeler.evaluate.wearer_renders`` (the AR harness), replaced by generated PNGs so the wearer path runs offline.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
from pathlib import Path
import shutil
import unittest
from unittest import mock

from PIL import Image, ImageDraw

from modeler import evaluate as mevaluate
from modeler.agentic import config, evaluation
from modeler.agentic.artifacts import AUTHOR_VISIBLE, HOST_ONLY, SEALED, SYNTHETIC, AccessDenied, ArtifactStore
from modeler.agentic.budget import Budget
from modeler.agentic.evaluation import (EvidenceError, assert_no_sealed_pixels, critic_blocks, final_axes, final_blocks, record_owner_verdict,
                                        reserve_sealed_evidence, validate_critique, write_deliverable)
from modeler.agentic.pricing import Tariff
from modeler.agentic.responses import ENDPOINT, image_block, text_block
from modeler.agentic.state import Store
from test_agentic_support import fresh_dir


RATIONALE = "RATIONALE-SENTINEL-7f3a-the-author-explains-itself"
GLB_BYTES = b"glTF-fixture-bytes-" + bytes(range(64))
GLB_SHA = hashlib.sha256(GLB_BYTES).hexdigest()
PROGRAM_SHA = "ab" * 32
VIEWS = (("front", (230, 230, 230), False), ("back", (210, 220, 230), False), ("left", (220, 230, 210), False), ("angled", (255, 0, 255), True))


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def pix(data: bytes) -> str:
    return evaluation.pixel_sha256(data)[0]


def make_png(path: Path, colour, text: str, size=(96, 64)) -> bytes:
    im = Image.new("RGB", size, colour)
    d = ImageDraw.Draw(im)
    d.rectangle([8, 14, 40, 50], outline=(30, 30, 40), width=3)
    d.rectangle([52, 14, 88, 50], outline=(30, 30, 40), width=3)
    d.text((4, 1), text, fill=(0, 0, 0))
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path)
    return path.read_bytes()


def image_blocks(blocks: list[dict]) -> list[bytes]:
    out = []
    for b in blocks:
        if b.get("type") == "input_image":
            out.append(base64.b64decode(str(b["image_url"]).split(",", 1)[1]))
    return out


def texts(blocks: list[dict]) -> list[str]:
    return [b["text"] for b in blocks if b.get("type") == "input_text"]


def evaluation_record(overall: str = "accept") -> dict:
    return mevaluate.validate_evaluation({"discrepancies": [], "identity_checklist": [], "runtime_notes": "", "overall": overall, "summary": "fixture",
                                          "resemblance_0_10": {"front": 8, "side": 8, "angled_held_out": 8, "materials_and_lenses": 8, "mirror_overall": 8}})


class _JobBase(unittest.TestCase):
    """A fresh job folder per test; every Store is closed before the folder is removed (SQLite on Windows)."""

    def setUp(self):
        self.root = fresh_dir(None, "evaluation", self.__class__.__name__[:12] + "-")
        self.stores: list[Store] = []
        self.photo_paths: dict[str, Path] = {}
        self.photo_bytes: dict[str, bytes] = {}
        for i, (view, colour, _held) in enumerate(VIEWS):
            p = self.root / "photos" / f"{view}.png"
            self.photo_bytes[view] = make_png(p, colour, f"photo {view} #{i}")
            self.photo_paths[view] = p

    def tearDown(self):
        for s in self.stores:
            try:
                s.close()
            except Exception:  # noqa: BLE001
                pass
        shutil.rmtree(self.root, ignore_errors=True)

    # ------------------------------------------------------------------ job / store
    def make_store(self, name: str = "job", *, policy: dict | None = None) -> Store:
        policy = policy or config.build_policy(owner_review=False, driver="scripted", worker="fake", intake="synthetic", critic="scripted", final_evaluator="scripted", ar=False,
                                               budget_usd="5")
        tariff = Tariff.frozen(service_tier=policy["service_tier"], region=policy["region"])
        request = {"product_id": "fixture", "photos": [{"id": v, "view": v, "held_out": h} for v, _c, h in VIEWS], "dimensions": {}, "notes": "fixture notes"}
        store = Store.create(self.root / name, {"request": request, "policy": policy, "fingerprints": {"protocol": "test-fingerprints"}, "model": policy["model"],
                                                "reasoning_effort": policy["reasoning_effort"], "service_tier": policy["service_tier"], "endpoint": ENDPOINT,
                                                "tariff": tariff.to_dict(), "cap_micro": policy["cap_micro"], "inference_operation_cap": policy["max_inference_requests"],
                                                "driver": policy["driver"], "worker": policy["worker"], "worker_config": None})
        self.stores.append(store)
        return store

    def photo_specs(self, held_out=("angled",), views=("front", "back", "left", "angled")) -> list[dict]:
        return [{"id": v, "path": str(self.photo_paths[v]), "view": v, "held_out": v in held_out} for v in views]

    def reserve(self, store: Store, artifacts: ArtifactStore, photos: list[dict] | None = None) -> dict:
        return reserve_sealed_evidence(store, artifacts, photos if photos is not None else self.photo_specs())

    # ------------------------------------------------------------------ revisions
    def add_revision(self, store: Store, *, synthetic: bool, compatible: bool, glb: bytes | None = None, glb_sha256: str | None = None,
                     summary: dict | None = None, bbox=None, views: dict | None = None, parent: str | None = None, rationale: str = RATIONALE) -> dict:
        rev = store.insert_revision(parent_id=parent, program_set_sha256=PROGRAM_SHA, modules={"frame": {"sha256": "cd" * 32, "bytes": 10}}, rationale=rationale,
                                    synthetic=synthetic)
        comp = {"synthetic": synthetic, "compatible": compatible, "reasons": [] if compatible else ["ar_not_compatible_or_report_invalid"], "contract_ok": not synthetic}
        obs = {"summary": summary if summary is not None else {"status": "unmeasured"}, "bbox_mm": bbox, "views": views or {}}
        rdir = store.job_dir / "revisions" / rev["id"]
        rdir.mkdir(parents=True, exist_ok=True)
        if glb is not None:
            (rdir / "model.glb").write_bytes(glb)
        store.update_revision(rev["id"], state="compatible" if compatible else "incompatible", compatibility_json=comp, observation_json=obs,
                              glb_sha256=glb_sha256 if glb_sha256 is not None else (sha(glb) if glb is not None else None))
        return store.revision(rev["id"])

    def rev_dir(self, store: Store, rev: dict) -> Path:
        return store.job_dir / "revisions" / rev["id"]

    def add_image(self, artifacts: ArtifactStore, rev: dict | None, *, kind: str, role: str, view: str, colour=(120, 140, 160), label: str | None = None,
                  recipe: dict | None = None) -> dict:
        tag = f"{kind}-{role}-{view}"
        data = make_png(self.root / "scratch" / f"{tag}-{len(list((self.root / 'scratch').glob('*'))) if (self.root / 'scratch').is_dir() else 0}.png", colour, tag)
        return artifacts.add_bytes(data, kind=kind, role=role, suffix=".png", label=label or f"{rev['id'] if rev else 'x'}: {tag}",
                                   revision_id=rev["id"] if rev else None, recipe=dict({"view": view}, **(recipe or {})), synthetic=(role == SYNTHETIC))

    def deliver(self, store: Store, artifacts: ArtifactStore, rev: dict | None, *, axes=None, limitations=("fixture limitation",), stop_reason="delivered_by_author"):
        return write_deliverable(store, artifacts, rev, revision_dir=self.rev_dir(store, rev) if rev else None, final={"evaluation": None, "meta": {}, "delivery": None},
                                 axes=axes, stop_reason=stop_reason, budget_summary=Budget(store).totals(), limitations=list(limitations))


# =============================================================================== sealed reservation
class ReserveSealedEvidenceTests(_JobBase):
    def test_held_out_photo_lands_in_sealed_area_and_the_rest_in_artifacts(self):
        store = self.make_store()
        arts = ArtifactStore(store)
        out = self.reserve(store, arts)
        self.assertEqual(out["sealed_ids"], ["angled"])
        self.assertEqual(out["author_ids"], ["front", "back", "left"])
        sealed_rows = store.artifacts(role=SEALED)
        author_rows = store.artifacts(role=AUTHOR_VISIBLE)
        self.assertEqual(len(sealed_rows), 1)
        self.assertEqual(len(author_rows), 3)
        sealed = sealed_rows[0]
        self.assertTrue(sealed["rel_path"].startswith("sealed/photo/"), sealed["rel_path"])
        self.assertTrue((store.job_dir / sealed["rel_path"]).is_file())
        self.assertIn("SEALED", sealed["label"])
        self.assertEqual(sealed["sha256"], sha(self.photo_bytes["angled"]))
        for a in author_rows:
            self.assertTrue(a["rel_path"].startswith("artifacts/photo/"), a["rel_path"])
            self.assertTrue((store.job_dir / a["rel_path"]).is_file())
            self.assertNotIn("SEALED", a["label"])
        self.assertFalse(list((store.job_dir / "sealed").rglob("front*")))
        setting = store.setting("sealed_reservation")
        self.assertEqual(setting["sealed_pixel_sha256"], [pix(self.photo_bytes["angled"])])
        self.assertEqual(setting["sealed_ids"], ["angled"])
        by_id = {p["id"]: p for p in setting["photos"]}
        self.assertEqual(by_id["angled"]["artifact_id"], sealed["id"])
        self.assertTrue(by_id["angled"]["sealed"])
        self.assertFalse(by_id["front"]["sealed"])
        self.assertEqual([e["data"] for e in store.events("sealed_reserved")], [{"sealed": ["angled"], "author": ["front", "back", "left"]}])
        # the boundary: an author-role reader cannot open the sealed row
        with self.assertRaises(AccessDenied):
            arts.read(sealed["id"], allow_roles=(AUTHOR_VISIBLE,))
        row, data = arts.read(sealed["id"], allow_roles=(SEALED,))
        self.assertEqual(data, self.photo_bytes["angled"])

    def test_reencoded_png_copy_of_the_sealed_photo_among_author_photos_fails_closed(self):
        store = self.make_store()
        arts = ArtifactStore(store)
        copy = self.root / "photos" / "angled_copy.png"
        with Image.open(self.photo_paths["angled"]) as im:
            im.save(copy, compress_level=0)
        self.assertNotEqual(sha(copy.read_bytes()), sha(self.photo_bytes["angled"]), "the fixture must be a different byte stream")
        self.assertEqual(pix(copy.read_bytes()), pix(self.photo_bytes["angled"]), "...with the same pixels")
        photos = self.photo_specs() + [{"id": "extra", "path": str(copy), "view": "other", "held_out": False}]
        with self.assertRaises(EvidenceError) as cm:
            self.reserve(store, arts, photos)
        self.assertIn("same pixels", str(cm.exception))
        self.assertEqual(store.artifacts(), [], "nothing is catalogued when the request is inconsistent")
        self.assertIsNone(store.setting("sealed_reservation"))

    def test_lossless_webp_copy_of_the_sealed_photo_is_recognised(self):
        store = self.make_store()
        arts = ArtifactStore(store)
        copy = self.root / "photos" / "angled_copy.webp"
        with Image.open(self.photo_paths["angled"]) as im:
            im.save(copy, lossless=True)
        self.assertEqual(pix(copy.read_bytes()), pix(self.photo_bytes["angled"]))
        photos = self.photo_specs() + [{"id": "extra", "path": str(copy), "view": "other", "held_out": False}]
        with self.assertRaises(EvidenceError):
            self.reserve(store, arts, photos)
        self.assertEqual(store.artifacts(), [])

    def test_declared_crop_of_the_sealed_photo_is_sealed(self):
        store = self.make_store()
        arts = ArtifactStore(store)
        crop = self.root / "photos" / "angled_crop.png"
        with Image.open(self.photo_paths["angled"]) as im:
            im.crop((10, 10, 70, 50)).save(crop)
        self.assertNotEqual(pix(crop.read_bytes()), pix(self.photo_bytes["angled"]), "a crop has different pixels: only the ancestry can seal it")
        photos = self.photo_specs() + [{"id": "crop1", "path": str(crop), "view": "other", "held_out": False, "source_photo_id": "angled", "crop_xyxy": [10, 10, 70, 50]}]
        out = self.reserve(store, arts, photos)
        self.assertEqual(out["sealed_ids"], ["angled", "crop1"])
        row = store.artifact(next(p["artifact_id"] for p in out["photos"] if p["id"] == "crop1"))
        self.assertEqual(row["role"], SEALED)
        self.assertTrue(row["rel_path"].startswith("sealed/photo/"))
        self.assertIn("SEALED", row["label"])
        self.assertEqual(row["recipe"]["source_photo_id"], "angled")
        self.assertIn(pix(crop.read_bytes()), store.setting("sealed_reservation")["sealed_pixel_sha256"])
        # a crop's declared parent leaves the author's photos alone
        self.assertEqual(out["author_ids"], ["front", "back", "left"])

    def test_declared_crop_of_an_author_photo_stays_author_visible(self):
        store = self.make_store()
        arts = ArtifactStore(store)
        crop = self.root / "photos" / "front_crop.png"
        with Image.open(self.photo_paths["front"]) as im:
            im.crop((0, 0, 50, 40)).save(crop)
        photos = self.photo_specs() + [{"id": "fc", "path": str(crop), "view": "other", "held_out": False, "source_photo_id": "front"}]
        out = self.reserve(store, arts, photos)
        self.assertIn("fc", out["author_ids"])
        self.assertEqual(store.artifact(next(p["artifact_id"] for p in out["photos"] if p["id"] == "fc"))["role"], AUTHOR_VISIBLE)

    def test_unknown_parent_fails(self):
        store = self.make_store()
        arts = ArtifactStore(store)
        photos = self.photo_specs() + [{"id": "orphan", "path": str(self.photo_paths["front"]), "view": "other", "held_out": False, "source_photo_id": "nope"}]
        with self.assertRaises(EvidenceError) as cm:
            self.reserve(store, arts, photos)
        self.assertIn("unknown or cyclic parent", str(cm.exception))
        self.assertEqual(store.artifacts(), [])

    def test_every_photo_sealed_fails(self):
        store = self.make_store()
        arts = ArtifactStore(store)
        with self.assertRaises(EvidenceError) as cm:
            self.reserve(store, arts, self.photo_specs(held_out=("front", "back", "left", "angled")))
        self.assertIn("every photo is sealed", str(cm.exception))
        self.assertEqual(store.artifacts(), [])

    def test_duplicate_photo_id_fails(self):
        store = self.make_store()
        arts = ArtifactStore(store)
        photos = self.photo_specs() + [{"id": "front", "path": str(self.photo_paths["back"]), "view": "other", "held_out": False}]
        with self.assertRaises(EvidenceError):
            self.reserve(store, arts, photos)


# =============================================================================== the leak sentinel
class AssertNoSealedPixelsTests(_JobBase):
    def test_raises_on_a_block_carrying_the_sealed_pixels_in_any_encoding(self):
        store = self.make_store()
        self.reserve(store, ArtifactStore(store))
        with self.assertRaises(EvidenceError):
            assert_no_sealed_pixels(store, [text_block("x"), image_block(self.photo_bytes["angled"], "image/png")])
        buf = io.BytesIO()
        with Image.open(self.photo_paths["angled"]) as im:
            im.save(buf, format="WEBP", lossless=True)
        with self.assertRaises(EvidenceError):
            assert_no_sealed_pixels(store, [image_block(buf.getvalue(), "image/webp")])

    def test_passes_on_author_photos_and_text(self):
        store = self.make_store()
        self.reserve(store, ArtifactStore(store))
        blocks = [text_block(json.dumps({"note": "angled"}))] + [image_block(self.photo_bytes[v], "image/png") for v in ("front", "back", "left")]
        assert_no_sealed_pixels(store, blocks)      # no exception

    def test_no_reservation_means_nothing_is_sealed(self):
        store = self.make_store()
        assert_no_sealed_pixels(store, [image_block(self.photo_bytes["angled"], "image/png")])


# =============================================================================== critic
class CriticBlocksTests(_JobBase):
    def test_critic_sees_author_photos_and_the_revision_renders_only(self):
        store = self.make_store()
        arts = ArtifactStore(store)
        reservation = self.reserve(store, arts)
        summary = {"front_contour_mean_mm": 0.42, "lens_outline_mean_mm": 0.31}
        rev = self.add_revision(store, synthetic=False, compatible=True, glb=GLB_BYTES, summary=summary)
        other = self.add_revision(store, synthetic=False, compatible=False, rationale="other revision")
        render = self.add_image(arts, rev, kind="render", role=AUTHOR_VISIBLE, view="front")
        sheet = self.add_image(arts, rev, kind="sheet", role=AUTHOR_VISIBLE, view="photo_match")
        sealed_render = self.add_image(arts, rev, kind="render", role=SEALED, view="heldout_angled", colour=(250, 10, 250))
        host_sheet = self.add_image(arts, rev, kind="sheet", role=HOST_ONLY, view="final")
        foreign = self.add_image(arts, other, kind="render", role=AUTHOR_VISIBLE, view="front", colour=(10, 10, 10))
        blocks = critic_blocks(store, arts, {"notes": "fixture notes"}, rev, "what is visibly wrong?")
        text = "\n".join(texts(blocks))
        head = json.loads(texts(blocks)[0])
        self.assertEqual(head["task"], "critique")
        self.assertEqual(head["revision"], rev["id"])
        self.assertEqual(head["question"], "what is visibly wrong?")
        self.assertEqual(head["measurements"], summary)
        self.assertEqual(head["compatibility"], rev["compatibility"])
        pixels = [pix(d) for d in image_blocks(blocks)]
        author_ids = [p["artifact_id"] for p in reservation["photos"] if not p["sealed"]]
        for v, aid in zip(("front", "back", "left"), author_ids):
            self.assertIn(f'"image_id": "{aid}"', text)
            self.assertIn(pix(self.photo_bytes[v]), pixels)
        for a in (render, sheet):
            self.assertIn(f'"image_id": "{a["id"]}"', text)
            self.assertIn(pix(arts.read(a["id"], allow_roles=(AUTHOR_VISIBLE,))[1]), pixels)
        self.assertNotIn(pix(self.photo_bytes["angled"]), pixels, "the sealed photograph's pixels reached the critic")
        for a in (sealed_render, host_sheet, foreign):
            self.assertNotIn(a["id"], text)
        self.assertNotIn(RATIONALE, text)
        self.assertNotIn("rationale", text.lower())
        self.assertEqual(len(pixels), 5)


class ValidateCritiqueTests(unittest.TestCase):
    def test_major_defect_fails_minor_passes_bad_severity_rejected(self):
        base = {"matches": ["a"], "uncertainty": "u", "suggested_repairs": [], "summary": "s"}
        self.assertEqual(validate_critique(dict(base, defects=[{"part": "frame", "where_seen": "x", "description": "d", "severity": "major"}]))["verdict"], "fail")
        self.assertEqual(validate_critique(dict(base, defects=[{"part": "frame", "where_seen": "x", "description": "d", "severity": "minor"}]))["verdict"], "pass")
        self.assertEqual(validate_critique(dict(base, defects=[]))["verdict"], "pass")
        with self.assertRaises(ValueError):
            validate_critique(dict(base, defects=[{"part": "frame", "severity": "fatal"}]))
        with self.assertRaises(ValueError):
            validate_critique(["not", "an", "object"])


# =============================================================================== final evaluator message
class FinalBlocksTests(_JobBase):
    PROTOCOL = {"identity_checklist": ["a distinctive brow bar", "keyhole bridge"], "checklist_source": "intake_reading", "visual_bar_calibrated": False,
                "calibration_summary": {"calibrated": False, "note": "owner accepted 5 of 10 assets"}, "expected_verdict": "accept",
                "gate_thresholds_mm": mevaluate.GATE_THRESHOLDS_MM, "input_flags": []}

    def test_final_message_carries_all_photos_and_the_frozen_checklist_but_no_rationale_or_expectation(self):
        store = self.make_store()
        arts = ArtifactStore(store)
        reservation = self.reserve(store, arts)
        summary = {"front_contour_mean_mm": 0.4, "lens_outline_mean_mm": 0.3, "ar_runtime_compatible": True, "ar_optical_meshes": 2, "ar_continuity_failure": False}
        views = {"front": {"view": "front", "iou": 0.93, "contour_mean_mm": 0.4, "contour_p95_mm": 1.1, "camera": "not for the evaluator"}}
        rev = self.add_revision(store, synthetic=False, compatible=True, glb_sha256=GLB_SHA, summary=summary, views=views)   # no model.glb: no wearer run
        rdir = self.rev_dir(store, rev)
        (rdir / "heldout").mkdir()
        (rdir / "heldout" / "heldout.json").write_text(json.dumps({"summary": {"mean_contour_mm_all_fit_views": 1.2}, "renders": {}}), encoding="utf-8")
        match = self.add_image(arts, rev, kind="sheet", role=AUTHOR_VISIBLE, view="photo_match")
        ar_sheet = self.add_image(arts, rev, kind="sheet", role=AUTHOR_VISIBLE, view="ar")
        sealed_render = self.add_image(arts, rev, kind="render", role=SEALED, view="heldout_angled", colour=(250, 10, 250))
        blocks, bindings = final_blocks(store, arts, {"notes": "fixture notes"}, rev, self.PROTOCOL, revision_dir=rdir)
        text = "\n".join(texts(blocks))
        request = json.loads(texts(blocks)[0])
        self.assertEqual(request["protocol"], mevaluate.PROTOCOL)
        self.assertEqual(request["identity_checklist"], ["a distinctive brow bar", "keyhole bridge"])
        self.assertEqual(request["measurements"]["author_visible"], summary)
        self.assertEqual(request["measurements"]["held_out"], {"mean_contour_mm_all_fit_views": 1.2})
        self.assertEqual(request["measurements"]["per_view"], {"front": {"view": "front", "iou": 0.93, "contour_mean_mm": 0.4, "contour_p95_mm": 1.1}})
        self.assertEqual(request["runtime"], {"runtime_compatible": True, "optical_meshes_detected": 2, "temple_continuity_failure": False})
        pixels = [pix(d) for d in image_blocks(blocks)]
        by_id = {p["id"]: p for p in reservation["photos"]}
        for v in ("front", "back", "left", "angled"):
            self.assertIn(f'"image_id": "{by_id[v]["artifact_id"]}"', text)
            self.assertIn(pix(self.photo_bytes[v]), pixels)
        with self.assertRaises(EvidenceError):
            assert_no_sealed_pixels(store, blocks)      # the sealed evaluator is the one reader of the sealed pixels
        self.assertIn(match["id"], text)
        self.assertIn(sealed_render["id"], text)
        self.assertNotIn(ar_sheet["id"], text, "only photo_match sheets and sealed renders of the revision travel")
        low = text.lower()
        for forbidden in ("rationale", "expected", "owner accepted", RATIONALE.lower(), "not for the evaluator"):
            self.assertNotIn(forbidden, low, forbidden)
        self.assertNotIn("SEALED:", text)
        self.assertIn("(held out from the author)", text)
        # bindings: the exact frozen candidate, the evidence and the protocol
        self.assertEqual(bindings["revision"], rev["id"])
        self.assertEqual(bindings["asset_sha256"], GLB_SHA)
        self.assertEqual(bindings["program_set_sha256"], PROGRAM_SHA)
        self.assertFalse(bindings["synthetic"])
        self.assertEqual(bindings["identity_checklist_source"], "intake_reading")
        wr = bindings["wearer_renders"]
        self.assertEqual({k: wr[k] for k in ("count", "error", "cache_key")}, {"count": 0, "error": None, "cache_key": None})
        self.assertFalse(bindings["evidence_complete"], "no wearer renders: the evidence is incomplete")
        imgs = {i["id"]: i for i in bindings["images"]}
        self.assertEqual(imgs[by_id["angled"]["artifact_id"]], {"id": by_id["angled"]["artifact_id"], "sha256": sha(self.photo_bytes["angled"]), "role": SEALED})
        self.assertEqual(imgs[by_id["front"]["artifact_id"]]["role"], AUTHOR_VISIBLE)
        self.assertEqual(imgs[match["id"]]["sha256"], match["sha256"])
        self.assertEqual(imgs[sealed_render["id"]]["role"], SEALED)
        self.assertEqual(len(imgs), 6)
        proto = bindings["protocol"]
        self.assertEqual(proto["protocol"], mevaluate.PROTOCOL)
        self.assertEqual(proto["gate_thresholds_mm"], mevaluate.GATE_THRESHOLDS_MM)
        self.assertEqual(proto["report_only_mm"], mevaluate.REPORT_ONLY_MM)
        for k in ("tool_schema_sha256", "task_sha256"):
            self.assertRegex(proto[k], r"^[0-9a-f]{64}$")
        self.assertEqual(proto, evaluation.FINAL_PROTOCOL)

    def test_synthetic_revision_never_runs_the_wearer_harness_even_with_a_glb(self):
        store = self.make_store()
        arts = ArtifactStore(store)
        self.reserve(store, arts)
        rev = self.add_revision(store, synthetic=True, compatible=True, glb=GLB_BYTES)
        with mock.patch.object(evaluation.mevaluate, "wearer_renders", side_effect=AssertionError("the harness must not run")) as harness:
            blocks, bindings = final_blocks(store, arts, {}, rev, self.PROTOCOL, revision_dir=self.rev_dir(store, rev))
        harness.assert_not_called()
        self.assertTrue(bindings["synthetic"])
        self.assertEqual(bindings["wearer_renders"]["count"], 0)

    def test_wearer_renders_become_host_only_sheets_bound_to_the_revision(self):
        """A real candidate with model.glb: the wearer renders (harness patched to generated PNGs) are stitched into
        two HOST_ONLY sheets under revisions/<id>/final, catalogued on the revision and carried with the cache key."""
        store = self.make_store()
        arts = ArtifactStore(store)
        self.reserve(store, arts)
        rev = self.add_revision(store, synthetic=False, compatible=True, glb=GLB_BYTES, bbox=[[-70.0, -20.0, -5.0], [70.0, 20.0, 5.0]])
        rdir = self.rev_dir(store, rev)
        renders = []
        for view in ("front", "angled", "rolled"):
            p = rdir / "observe" / "ar_wearer" / f"candidate__{view}.png"
            p.parent.mkdir(parents=True, exist_ok=True)
            im = Image.new("RGB", (160, 120), mevaluate._hex_rgb(mevaluate.WEARER_BACKGROUND))
            ImageDraw.Draw(im).rectangle([40, 40, 120, 80], fill=(20, 20, 30))
            im.save(p)
            renders.append(str(p))
        cache_key = {"asset_sha256": GLB_SHA, "views": ["front", "angled", "rolled"]}
        (rdir / "observe" / "ar_wearer" / "archeck.json").write_text(json.dumps({"cache_key": cache_key}), encoding="utf-8")
        calls = []

        def fake_wearer_renders(asset_dir, glb_path, width_mm=None, *, force=False):
            calls.append((Path(asset_dir), Path(glb_path), width_mm, force))
            return {"renders": renders}

        with mock.patch.object(evaluation.mevaluate, "wearer_renders", fake_wearer_renders):
            blocks, bindings = final_blocks(store, arts, {}, rev, self.PROTOCOL, revision_dir=rdir)
        self.assertEqual(calls, [(rdir, rdir / "model.glb", 140.0, True)])
        sheets = [a for a in store.artifacts(revision_id=rev["id"], kind="sheet") if a["role"] == HOST_ONLY]
        self.assertEqual(len(sheets), 2)
        for a in sheets:
            self.assertTrue(a["recipe"]["final"])
            self.assertTrue((store.job_dir / a["rel_path"]).is_file())
            self.assertIn(a["id"], "\n".join(texts(blocks)))
        self.assertTrue((rdir / "final" / "sheet_wearer_mirror.png").is_file())
        self.assertTrue((rdir / "final" / "sheet_wearer_detail.png").is_file())
        wr = bindings["wearer_renders"]
        self.assertEqual({k: wr[k] for k in ("count", "error", "cache_key")}, {"count": 3, "error": None, "cache_key": cache_key})
        self.assertEqual((wr["listed"], wr["expected"], wr["sheets"]), (3, 3, 2), "the binding counts render files on disk and the sheets appended")
        # this fixture's observation carries no AR report, so the evidence is incomplete for that reason alone, never for the wearer renders
        self.assertTrue(bindings["evidence_problems"] and all("observation summary" in s for s in bindings["evidence_problems"]), bindings["evidence_problems"])
        self.assertEqual({i["id"] for i in bindings["images"] if i["role"] == HOST_ONLY}, {a["id"] for a in sheets})
        self.assertEqual(len(image_blocks(blocks)), 4 + 2)

    def test_wearer_harness_failure_is_recorded_in_the_bindings_not_raised(self):
        store = self.make_store()
        arts = ArtifactStore(store)
        self.reserve(store, arts)
        rev = self.add_revision(store, synthetic=False, compatible=True, glb=GLB_BYTES)
        with mock.patch.object(evaluation.mevaluate, "wearer_renders", side_effect=RuntimeError("no node on this host")):
            blocks, bindings = final_blocks(store, arts, {}, rev, self.PROTOCOL, revision_dir=self.rev_dir(store, rev))
        self.assertEqual(bindings["wearer_renders"]["count"], 0)
        self.assertIn("RuntimeError: no node on this host", bindings["wearer_renders"]["error"])
        self.assertEqual(len(image_blocks(blocks)), 4)
        self.assertEqual([a for a in store.artifacts(revision_id=rev["id"]) if a["role"] == HOST_ONLY], [])


# =============================================================================== the verdict axes
class FinalAxesTests(_JobBase):
    def protocol(self, calibrated=False):
        return {"visual_bar_calibrated": calibrated, "input_flags": [], "gate_overrides": {}}

    def test_synthetic_revision_is_unmeasured_and_never_compatible(self):
        store = self.make_store()
        rev = self.add_revision(store, synthetic=True, compatible=True)
        axes = final_axes(rev, evaluation_record("accept"), self.protocol(calibrated=True), evaluation_error=None)
        self.assertEqual(axes["visual"]["automatic_verdict"], "unmeasured")
        self.assertEqual(axes["visual"]["status"], "quality_unverified")
        self.assertEqual(axes["compatibility"], {"compatible": False, "reasons": [], "synthetic": True})
        self.assertIsNone(axes["owner"]["verdict"])

    def test_missing_evaluation_is_unmeasured_with_the_error(self):
        store = self.make_store()
        rev = self.add_revision(store, synthetic=False, compatible=True, glb_sha256=GLB_SHA)
        axes = final_axes(rev, None, self.protocol(), evaluation_error="budget exhausted before the final evaluation")
        self.assertEqual(axes["visual"]["automatic_verdict"], "unmeasured")
        self.assertEqual(axes["visual"]["status"], "quality_unverified")
        self.assertTrue(any("budget exhausted before the final evaluation" in r for r in axes["visual"]["reasons"]))
        self.assertTrue(axes["compatibility"]["compatible"], "compatibility does not depend on the visual axis")

    def test_evaluator_accept_without_the_lens_outline_gate_metric_is_not_an_accept(self):
        store = self.make_store()
        rev = self.add_revision(store, synthetic=False, compatible=True, glb_sha256=GLB_SHA,
                                summary={"front_contour_mean_mm": 0.4, "ar_runtime_compatible": True, "ar_continuity_failure": False})
        axes = final_axes(rev, evaluation_record("accept"), self.protocol(), evaluation_error=None)
        visual = axes["visual"]
        self.assertNotEqual(visual["automatic_verdict"], "accept")
        self.assertEqual(visual["automatic_verdict"], "unmeasured")
        self.assertNotEqual(visual["status"], "accepted")
        self.assertTrue(any("lens_outline_mean_mm" in r and "unmeasured" in r for r in visual["reasons"]), visual["reasons"])
        self.assertIsNone(visual["provisional"]["lens_outline_mean_mm"]["pass"])
        self.assertEqual(visual["evaluator_overall"], "accept")

    def test_clean_accept_is_never_accepted_while_the_visual_bar_is_uncalibrated(self):
        store = self.make_store()
        summary = {"front_contour_mean_mm": 0.4, "lens_outline_mean_mm": 0.3, "ar_runtime_compatible": True, "ar_continuity_failure": False}
        rev = self.add_revision(store, synthetic=False, compatible=True, glb_sha256=GLB_SHA, summary=summary)
        axes = final_axes(rev, evaluation_record("accept"), self.protocol(calibrated=False), evaluation_error=None)
        self.assertEqual(axes["visual"]["automatic_verdict"], "accept")
        self.assertEqual(axes["visual"]["status"], "best_effort")
        self.assertTrue(any("not calibrated" in r for r in axes["visual"]["reasons"]))
        calibrated = final_axes(rev, evaluation_record("accept"), self.protocol(calibrated=True), evaluation_error=None)
        self.assertEqual(calibrated["visual"]["status"], "accepted", "the same evidence with a calibrated bar: the flag is what withholds 'accepted'")
        self.assertIsNone(calibrated["owner"]["verdict"], "an automatic accept never writes the owner axis")

    def test_evaluator_reject_and_failed_gate_are_rejects(self):
        store = self.make_store()
        good = {"front_contour_mean_mm": 0.4, "lens_outline_mean_mm": 0.3}
        rev = self.add_revision(store, synthetic=False, compatible=True, glb_sha256=GLB_SHA, summary=good)
        self.assertEqual(final_axes(rev, evaluation_record("reject"), self.protocol(True), evaluation_error=None)["visual"]["automatic_verdict"], "reject")
        bad = self.add_revision(store, synthetic=False, compatible=True, glb_sha256=GLB_SHA, summary=dict(good, lens_outline_mean_mm=2.5))
        axes = final_axes(bad, evaluation_record("accept"), self.protocol(True), evaluation_error=None)
        self.assertEqual(axes["visual"]["automatic_verdict"], "reject")
        self.assertNotEqual(axes["visual"]["status"], "accepted")

    def test_incompatible_real_revision_is_execution_failed_on_the_visual_axis(self):
        store = self.make_store()
        rev = self.add_revision(store, synthetic=False, compatible=False, glb_sha256=GLB_SHA, summary={"lens_outline_mean_mm": 0.3, "front_contour_mean_mm": 0.4})
        axes = final_axes(rev, evaluation_record("accept"), self.protocol(True), evaluation_error=None)
        self.assertFalse(axes["compatibility"]["compatible"])
        self.assertEqual(axes["compatibility"]["reasons"], ["ar_not_compatible_or_report_invalid"])
        self.assertEqual(axes["visual"]["status"], "execution_failed")
        self.assertEqual(axes["visual"]["automatic_verdict"], "reject")

    def test_the_three_axes_are_independent_fields(self):
        store = self.make_store()
        rev = self.add_revision(store, synthetic=False, compatible=True, glb_sha256=GLB_SHA)
        axes = final_axes(rev, None, self.protocol(), evaluation_error="not run")
        self.assertEqual(set(axes) - {"note"}, {"compatibility", "visual", "owner"})
        self.assertTrue(axes["compatibility"]["compatible"])
        self.assertEqual(axes["visual"]["automatic_verdict"], "unmeasured")
        self.assertEqual(axes["owner"], {"verdict": None, "note": "no owner verdict recorded"})


# =============================================================================== delivery
class WriteDeliverableTests(_JobBase):
    def test_compatible_revision_with_matching_glb_is_delivered_with_manifest_report_and_receipts(self):
        store = self.make_store()
        arts = ArtifactStore(store)
        self.reserve(store, arts)
        first = self.add_revision(store, synthetic=False, compatible=False)
        rev = self.add_revision(store, synthetic=False, compatible=True, glb=GLB_BYTES, parent=first["id"], summary={"lens_outline_mean_mm": 0.3})
        sheet = self.add_image(arts, rev, kind="sheet", role=AUTHOR_VISIBLE, view="ar")
        host = self.add_image(arts, rev, kind="sheet", role=HOST_ONLY, view="final")
        sealed_render = self.add_image(arts, rev, kind="render", role=SEALED, view="heldout_angled")
        store.enqueue_observation(sheet["id"], revision_id=rev["id"], operation_id=None, required=True)
        store.set_observation(sheet["id"], "acknowledged", acknowledged_request_id="q0007")
        store.update_job(selected_revision=rev["id"], current_revision=rev["id"])
        axes = final_axes(rev, evaluation_record("accept"), {"visual_bar_calibrated": False}, evaluation_error=None)
        manifest = self.deliver(store, arts, rev, axes=axes, limitations=["fixture limitation"])
        ddir = store.job_dir / "deliverable"
        self.assertTrue((ddir / "model.glb").is_file())
        self.assertEqual(sha((ddir / "model.glb").read_bytes()), GLB_SHA)
        self.assertEqual(manifest["deliverable_status"], "compatible_asset")
        self.assertEqual(manifest["asset"], {"path": str(ddir / "model.glb"), "sha256": GLB_SHA, "bytes": len(GLB_BYTES), "revision": rev["id"]})
        self.assertEqual(manifest["problems"], [])
        self.assertEqual(manifest["revision"]["id"], rev["id"])
        self.assertEqual(manifest["revision"]["glb_sha256"], GLB_SHA)
        self.assertEqual([(r["id"], r["compatible"]) for r in manifest["revisions"]], [(first["id"], False), (rev["id"], True)])
        self.assertEqual(manifest["observed_images"], [{"artifact_id": sheet["id"], "state": "acknowledged", "acknowledged_by": "q0007"}])
        self.assertEqual(manifest["limitations"], ["fixture limitation"])
        self.assertEqual(manifest["axes"], axes)
        self.assertEqual(manifest["selected_revision"], rev["id"])
        self.assertIn("cap_usd", manifest["budget"])
        self.assertEqual(manifest["fingerprints"], {"protocol": "test-fingerprints"})
        on_disk = json.loads((ddir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(on_disk["asset"], manifest["asset"])
        receipts = json.loads((ddir / "receipts.json").read_text(encoding="utf-8"))
        self.assertEqual(set(receipts), {"requests", "reservations", "operations"})
        report = (ddir / "report.md").read_text(encoding="utf-8")
        self.assertIn("## Verdict axes (independent)", report)
        self.assertIn("runtime compatibility", report)
        self.assertIn("automatic visual verdict", report)
        self.assertIn("owner", report)
        self.assertIn(GLB_SHA, report)
        self.assertIn("compatible_asset", report)
        self.assertIn("fixture limitation", report)
        copied = {p.name for p in (ddir / "images").iterdir()}
        self.assertEqual(copied, {Path(sheet["rel_path"]).name, Path(host["rel_path"]).name})
        self.assertNotIn(Path(sealed_render["rel_path"]).name, copied, "sealed renders never leave the sealed area")
        job = store.job()
        self.assertEqual(job["deliverable_status"], "compatible_asset")
        self.assertEqual(job["deliverable"]["asset"]["sha256"], GLB_SHA)
        self.assertEqual(store.events("deliverable_written")[-1]["data"]["status"], "compatible_asset")

    def test_changed_glb_bytes_are_not_delivered(self):
        store = self.make_store()
        arts = ArtifactStore(store)
        rev = self.add_revision(store, synthetic=False, compatible=True, glb=GLB_BYTES)
        (self.rev_dir(store, rev) / "model.glb").write_bytes(GLB_BYTES + b"\x00tampered")
        manifest = self.deliver(store, arts, rev)
        self.assertFalse((store.job_dir / "deliverable" / "model.glb").exists())
        self.assertIsNone(manifest["asset"])
        self.assertEqual(manifest["deliverable_status"], "none")
        self.assertTrue(any("changed" in p for p in manifest["problems"]), manifest["problems"])
        self.assertEqual(store.job()["deliverable_status"], "none")
        self.assertIn("problem:", (store.job_dir / "deliverable" / "report.md").read_text(encoding="utf-8"))

    def test_missing_glb_is_not_delivered(self):
        store = self.make_store()
        arts = ArtifactStore(store)
        rev = self.add_revision(store, synthetic=False, compatible=True, glb_sha256=GLB_SHA)
        manifest = self.deliver(store, arts, rev)
        self.assertFalse((store.job_dir / "deliverable" / "model.glb").exists())
        self.assertEqual(manifest["deliverable_status"], "none")
        self.assertTrue(any("missing" in p for p in manifest["problems"]), manifest["problems"])

    def test_synthetic_revision_never_yields_model_glb_even_with_a_matching_fake_glb(self):
        """Synthetic receipts cannot promote: a fake GLB planted in the synthetic revision's folder with a matching
        catalogue hash still delivers nothing."""
        store = self.make_store()
        arts = ArtifactStore(store)
        rev = self.add_revision(store, synthetic=True, compatible=True, glb=GLB_BYTES)
        self.assertEqual(rev["compatibility"], {"synthetic": True, "compatible": True, "reasons": [], "contract_ok": False})
        self.assertEqual(rev["glb_sha256"], GLB_SHA)
        self.assertTrue((self.rev_dir(store, rev) / "model.glb").is_file())
        manifest = self.deliver(store, arts, rev, axes=final_axes(rev, None, {}, evaluation_error="synthetic"))
        self.assertFalse((store.job_dir / "deliverable" / "model.glb").exists())
        self.assertIsNone(manifest["asset"])
        self.assertEqual(manifest["deliverable_status"], "synthetic_only")
        self.assertTrue(any("synthetic" in p.lower() for p in manifest["problems"]), manifest["problems"])
        self.assertTrue(manifest["revision"]["synthetic"])
        self.assertEqual(store.job()["deliverable_status"], "synthetic_only")
        self.assertIn("synthetic_only", (store.job_dir / "deliverable" / "report.md").read_text(encoding="utf-8"))

    def test_incompatible_real_revision_with_matching_glb_is_not_delivered(self):
        """Delivery is byte-bound AND compatibility-bound: a revision the contract or the AR check rejected must not
        become deliverable/model.glb labelled compatible_asset just because its GLB bytes match."""
        store = self.make_store()
        arts = ArtifactStore(store)
        rev = self.add_revision(store, synthetic=False, compatible=False, glb=GLB_BYTES)
        manifest = self.deliver(store, arts, rev, axes=final_axes(rev, None, {}, evaluation_error="not compatible"))
        self.assertFalse((store.job_dir / "deliverable" / "model.glb").exists(), "an incompatible revision was copied out as the deliverable")
        self.assertIsNone(manifest["asset"])
        self.assertNotEqual(manifest["deliverable_status"], "compatible_asset")
        self.assertTrue(any("not compatible" in p for p in manifest["problems"]), manifest["problems"])
        self.assertEqual(store.job()["deliverable_status"], manifest["deliverable_status"])

    def test_no_revision_writes_an_honest_empty_manifest(self):
        store = self.make_store()
        arts = ArtifactStore(store)
        manifest = self.deliver(store, arts, None, stop_reason="author_stopped_without_delivery")
        self.assertEqual(manifest["deliverable_status"], "none")
        self.assertIsNone(manifest["revision"])
        self.assertIsNone(manifest["asset"])
        self.assertEqual(manifest["observed_images"], [])
        self.assertEqual(manifest["stop_reason"], "author_stopped_without_delivery")
        ddir = store.job_dir / "deliverable"
        self.assertTrue((ddir / "manifest.json").is_file())
        self.assertTrue((ddir / "receipts.json").is_file())
        self.assertTrue((ddir / "report.md").is_file())
        self.assertFalse((ddir / "model.glb").exists())


# =============================================================================== owner verdicts
class OwnerVerdictTests(_JobBase):
    def delivered(self):
        store = self.make_store()
        arts = ArtifactStore(store)
        self.reserve(store, arts)
        rev = self.add_revision(store, synthetic=False, compatible=True, glb=GLB_BYTES, summary={"lens_outline_mean_mm": 0.3})
        sheet = self.add_image(arts, rev, kind="sheet", role=AUTHOR_VISIBLE, view="ar")        # delivery needs a revision the author has seen
        store.enqueue_observation(sheet["id"], revision_id=rev["id"], operation_id=None, required=True)
        store.set_observation(sheet["id"], "acknowledged", acknowledged_request_id="q0007")
        axes = final_axes(rev, evaluation_record("accept"), {"visual_bar_calibrated": False}, evaluation_error=None)
        self.deliver(store, arts, rev, axes=axes)
        return store, rev

    def test_needs_a_delivered_asset(self):
        store = self.make_store()
        arts = ArtifactStore(store)
        rev = self.add_revision(store, synthetic=True, compatible=True)
        self.deliver(store, arts, rev)
        with self.assertRaises(ValueError) as cm:
            record_owner_verdict(store, verdict="accept", sha256=None, medium="live AR")
        self.assertIn("no asset", str(cm.exception))
        self.assertEqual(store.verdicts(kind="owner"), [])
        with self.assertRaises(ValueError):
            record_owner_verdict(self.make_store("job2"), verdict="accept", sha256=None, medium="live AR")

    def test_accept_then_reject_revokes_acceptance_and_keeps_the_history(self):
        store, rev = self.delivered()
        first = record_owner_verdict(store, verdict="accept", sha256=None, medium="live AR mirror", note="looks right")
        self.assertEqual(first["kind"], "owner")
        self.assertEqual(first["verdict"], "accept")
        self.assertEqual(first["revision_id"], rev["id"])
        self.assertEqual(first["asset_sha256"], GLB_SHA)
        self.assertEqual(first["record"]["previous_owner_verdict"], None)
        self.assertFalse(first["record"]["revokes_acceptance"])
        self.assertEqual(first["record"]["medium"], "live AR mirror")
        self.assertEqual(first["bindings"]["asset_sha256"], GLB_SHA)
        self.assertEqual(first["bindings"]["deliverable_status"], "compatible_asset")
        owner = store.job()["deliverable"]["axes"]["owner"]
        self.assertEqual(owner["verdict"], "accept")
        self.assertEqual([h["verdict"] for h in owner["history"]], ["accept"])
        self.assertFalse(owner["revoked_acceptance"])
        second = record_owner_verdict(store, verdict="reject", sha256=GLB_SHA, medium="live AR mirror", note="temples float")
        self.assertTrue(second["record"]["revokes_acceptance"])
        self.assertEqual(second["record"]["previous_owner_verdict"], "accept")
        rows = store.verdicts(kind="owner")
        self.assertEqual([r["verdict"] for r in rows], ["accept", "reject"])
        self.assertEqual(rows[0], first, "append-only: the first row is untouched")
        for m in (store.job()["deliverable"], json.loads((store.job_dir / "deliverable" / "manifest.json").read_text(encoding="utf-8"))):
            owner = m["axes"]["owner"]
            self.assertEqual(owner["verdict"], "reject")
            self.assertEqual([h["verdict"] for h in owner["history"]], ["accept", "reject"])
            self.assertTrue(owner["revoked_acceptance"])
            self.assertEqual(m["asset"]["sha256"], GLB_SHA, "the rest of the manifest survives the verdict update")
            self.assertEqual(m["axes"]["visual"]["automatic_verdict"], "accept", "the visual axis is not rewritten by an owner verdict")
        self.assertEqual([e["data"]["verdict"] for e in store.events("owner_verdict")], ["accept", "reject"])

    def test_borderline_after_accept_also_revokes(self):
        store, _rev = self.delivered()
        record_owner_verdict(store, verdict="accept", sha256=None, medium="live AR")
        row = record_owner_verdict(store, verdict="borderline", sha256=None, medium="live AR")
        self.assertTrue(row["record"]["revokes_acceptance"])
        self.assertTrue(store.job()["deliverable"]["axes"]["owner"]["revoked_acceptance"])
        self.assertEqual(store.job()["deliverable"]["axes"]["owner"]["verdict"], "borderline")

    def test_reject_then_accept_revokes_nothing(self):
        store, _rev = self.delivered()
        record_owner_verdict(store, verdict="reject", sha256=None, medium="live AR")
        row = record_owner_verdict(store, verdict="accept", sha256=None, medium="live AR")
        self.assertFalse(row["record"]["revokes_acceptance"])
        self.assertEqual(row["record"]["previous_owner_verdict"], "reject")
        self.assertEqual([h["verdict"] for h in store.job()["deliverable"]["axes"]["owner"]["history"]], ["reject", "accept"])

    def test_refuses_when_the_delivered_bytes_changed(self):
        store, _rev = self.delivered()
        glb = store.job_dir / "deliverable" / "model.glb"
        glb.write_bytes(glb.read_bytes() + b"\x00")
        with self.assertRaises(ValueError) as cm:
            record_owner_verdict(store, verdict="accept", sha256=None, medium="live AR")
        self.assertIn("differ", str(cm.exception))
        self.assertEqual(store.verdicts(kind="owner"), [])
        self.assertIsNone(store.job()["deliverable"]["axes"]["owner"]["verdict"], "a refused verdict leaves the owner axis empty")
        glb.unlink()
        with self.assertRaises(ValueError):
            record_owner_verdict(store, verdict="accept", sha256=None, medium="live AR")
        self.assertEqual(store.verdicts(kind="owner"), [])

    def test_refuses_a_mismatching_digest_and_accepts_the_matching_one_in_any_case(self):
        store, _rev = self.delivered()
        with self.assertRaises(ValueError) as cm:
            record_owner_verdict(store, verdict="accept", sha256="00" * 32, medium="live AR")
        self.assertIn("does not match", str(cm.exception))
        self.assertEqual(store.verdicts(kind="owner"), [])
        row = record_owner_verdict(store, verdict="accept", sha256=GLB_SHA.upper(), medium="live AR")
        self.assertEqual(row["asset_sha256"], GLB_SHA)

    def test_rejects_an_unknown_verdict_word(self):
        store, _rev = self.delivered()
        with self.assertRaises(ValueError):
            record_owner_verdict(store, verdict="approve", sha256=None, medium="live AR")
        self.assertEqual(store.verdicts(kind="owner"), [])


# =============================================================================== end to end (offline demo session)
class OfflineDemoDeliveryTests(_JobBase):
    def test_demo_session_delivers_nothing_real_and_leaks_no_sealed_pixels(self):
        """The scripted demo through the real runner: synthetic_only, no model.glb, receipts written, every author and
        critic payload free of the held-out pixels, and an owner verdict refused for want of a delivered asset."""
        from modeler.agentic import demo, executor, responses, runner
        inputs = self.root / "inputs"
        request = demo.demo_request(inputs)
        translated = config.translate_request(request, inputs)
        policy = config.build_policy(owner_review=False, driver="scripted", worker="fake", budget_usd="5", max_inference_requests=12, max_output_tokens=4000, max_revisions=4,
                                     max_worker_seconds=120, wall_minutes=30, images_per_request=6, intake="synthetic", critic="scripted", final_evaluator="scripted",
                                     ar=False)
        sealed = demo.sealed_pixel_hashes(request["photos"])
        self.assertEqual(len(sealed), 1)
        transport = responses.ScriptedTransport(demo.demo_script(sealed))
        session = runner.Session.create(self.root / "job", translated=translated, policy=policy, fingerprints={"protocol": "test-fingerprints"},
                                        worker=executor.FakeWorker(demo.demo_fake_scenario()), transport=transport, worker_config=None, log=lambda *a, **k: None)
        self.stores.append(session.store)
        state = session.run()
        store = session.store
        self.assertEqual(state, "unresolved")
        self.assertEqual(store.job()["stop_reason"], "synthetic_demo_complete")
        ddir = store.job_dir / "deliverable"
        manifest = json.loads((ddir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["deliverable_status"], "synthetic_only")
        self.assertIsNone(manifest["asset"])
        self.assertFalse((ddir / "model.glb").exists())
        self.assertTrue((ddir / "receipts.json").is_file())
        self.assertIn("## Verdict axes (independent)", (ddir / "report.md").read_text(encoding="utf-8"))
        self.assertEqual(manifest["axes"]["visual"]["automatic_verdict"], "unmeasured")
        self.assertFalse(manifest["axes"]["compatibility"]["compatible"])
        self.assertTrue(manifest["axes"]["compatibility"]["synthetic"])
        self.assertEqual(len(store.artifacts(role=SEALED, kind="photo")), 1)
        self.assertEqual(len(list((store.job_dir / "sealed").rglob("*.png"))), 1)
        payloads = [s["payload"] for s in transport.sent if s["endpoint"] == "responses"]
        self.assertGreaterEqual(len(payloads), 8)
        for p in payloads:
            self.assertFalse(set(responses.payload_image_pixel_hashes(p)) & set(sealed), "a sealed photograph left the boundary")
        self.assertEqual([v["kind"] for v in store.verdicts()], ["critic"])
        with self.assertRaises(ValueError):
            record_owner_verdict(store, verdict="accept", sha256=None, medium="live AR")


if __name__ == "__main__":
    unittest.main()


# =============================================================================== acceptance evidence (2026-09-28)
class AcceptanceEvidenceTests(_JobBase):
    """'accepted' needs the asset's tags covered by the calibration set AND the wearer renders of the exact candidate.
    Before 2026-09-28 final_axes read tags/uncovered_tags from the protocol, which runner.freeze_protocol never writes
    (coverage lives in protocol['calibration_summary']['coverage']), so an uncovered translucent asset was 'accepted';
    and the wearer-render binding of run_final never reached final_axes, so a failed wearer harness still allowed
    'accepted'."""
    SUMMARY = {"front_contour_mean_mm": 0.4, "lens_outline_mean_mm": 0.3, "ar_runtime_compatible": True, "ar_report_valid": True,
               "ar_continuity_failure": False, "ar_optical_meshes": 2}
    EVIDENCE = {"front": {"layout": "pair", "rim_class": "full"}, "notes": "fixture notes"}
    TRANSLUCENT = {"materials": {"gl.material_frame": {"kind": "opaque"}, "gl.material_translucent": {"kind": "translucent"},
                                 "gl.material_lens": {"kind": "lens", "lens": {"mirror": None}}}}

    def protocol(self, *covered, calibrated=True, coverage=True):
        cs = {"calibrated": calibrated, "min_tag_verdicts": 2}
        if coverage:
            cs["coverage"] = {t: {"accepts": 2, "rejects": 1, "borderline": 0, "unevaluated": 0, "disagreements": 0, "covered": True} for t in covered}
            cs["coverage"]["mirrored"] = {"accepts": 1, "rejects": 0, "borderline": 0, "unevaluated": 0, "disagreements": 0, "covered": False}
            cs["covered_tags"] = list(covered)
        return {"visual_bar_calibrated": calibrated, "input_flags": [], "gate_overrides": {}, "calibration_summary": cs}

    def revision(self, store, *, summary=None, materials=TRANSLUCENT):
        rev = self.add_revision(store, synthetic=False, compatible=True, glb=GLB_BYTES, summary=self.SUMMARY if summary is None else summary,
                                bbox=[[-70.0, -20.0, -5.0], [70.0, 20.0, 5.0]])
        rdir = self.rev_dir(store, rev)
        if materials is not None:
            (rdir / "build").mkdir(parents=True, exist_ok=True)
            (rdir / "build" / "materials.json").write_text(json.dumps(materials), encoding="utf-8")
        return rev, rdir

    @staticmethod
    def meta(count=3, error=None):
        return {"request": "q0007", "bindings": {"wearer_renders": {"count": count, "error": error, "cache_key": {"asset_sha256": GLB_SHA} if count else None}}}

    def test_translucent_asset_without_coverage_of_translucent_is_not_accepted(self):
        store = self.make_store()
        rev, rdir = self.revision(store)
        axes = final_axes(rev, evaluation_record("accept"), self.protocol("pair", "rim_full"), evaluation_error=None, revision_dir=rdir,
                          evidence=self.EVIDENCE, final_meta=self.meta())
        visual = axes["visual"]
        self.assertEqual(visual["tags"], ["pair", "rim_full", "translucent"])
        self.assertEqual(visual["uncovered_tags"], ["translucent"])
        self.assertNotEqual(visual["status"], "accepted")
        self.assertEqual(visual["status"], "best_effort")
        self.assertEqual(visual["automatic_verdict"], "accept", "the verdict is the evaluator's; the STATUS withholds 'accepted'")
        self.assertTrue(any("translucent" in r and "coverage" in r for r in visual["reasons"]), visual["reasons"])
        self.assertTrue(axes["compatibility"]["compatible"])

    def test_listed_but_missing_wearer_renders_are_no_evidence(self):
        """Ghost renders: archeck lists render paths it never checks and wearer_sheets drops missing files; three listed but
        missing renders must bind as zero evidence, the evaluator's accept is recorded and not awarded, the gate table kept."""
        from unittest import mock
        import modeler.agentic.evaluation as evaluation_module
        store = self.make_store()
        arts = ArtifactStore(store)
        self.reserve(store, arts)
        rev, rdir = self.revision(store)
        ghosts = {"renders": [str(rdir / "observe" / "ar_wearer" / f"candidate__{v}.png") for v in ("front", "angled", "rolled")],
                  "status": "runtime_compatible", "runtime_compatible": True, "error": None}
        proto = self.protocol("pair", "rim_full", "translucent")
        with mock.patch.object(evaluation_module, "wearer_render_paths", return_value=ghosts):
            blocks, bindings = final_blocks(store, arts, self.EVIDENCE, rev, proto, revision_dir=rdir)
        wr = bindings["wearer_renders"]
        self.assertEqual((wr["count"], wr["listed"], wr["expected"], wr["sheets"]), (0, 3, 3, 0))
        self.assertIn("missing on disk", wr["error"])
        self.assertFalse(bindings["evidence_complete"])
        self.assertFalse(any("final" in json.loads(b["text"]).get("label", "") for b in blocks if b.get("type") == "input_text" and b["text"].startswith("{\"image_id\"")),
                         "no wearer sheet reached the evaluator message")
        axes = final_axes(rev, evaluation_record("accept"), proto, evaluation_error=None, revision_dir=rdir, evidence=self.EVIDENCE,
                          final_meta={"request": "q0009", "bindings": bindings})
        self.assertEqual((axes["visual"]["status"], axes["visual"]["automatic_verdict"]), ("quality_unverified", "unmeasured"))
        self.assertEqual(axes["visual"]["evaluator_overall"], "accept")
        self.assertIsNotNone(axes["visual"].get("provisional"), "the gate table stays in the report")
        self.assertFalse(axes["visual"]["wearer_evidence"]["complete"])

    def test_partial_or_incompatible_wearer_pass_is_not_accepted(self):
        store = self.make_store()
        rev, rdir = self.revision(store)
        proto = self.protocol("pair", "rim_full", "translucent")
        partial = {"request": "q", "bindings": {"wearer_renders": {"count": 1, "listed": 3, "expected": 3, "error": None, "cache_key": {}}}}
        rejected = {"request": "q", "bindings": {"wearer_renders": {"count": 3, "expected": 3, "error": None, "cache_key": {}, "status": "lens_rejected", "runtime_compatible": False}}}
        for meta in (partial, rejected):
            axes = final_axes(rev, evaluation_record("accept"), proto, evaluation_error=None, revision_dir=rdir, evidence=self.EVIDENCE, final_meta=meta)
            self.assertEqual(axes["visual"]["status"], "quality_unverified", meta)
            self.assertEqual(axes["visual"]["automatic_verdict"], "unmeasured", meta)
            self.assertIsNotNone(axes["visual"].get("provisional"), meta)
        complete = {"request": "q", "bindings": {"wearer_renders": {"count": 3, "expected": 3, "error": None, "cache_key": {}, "status": "runtime_compatible", "runtime_compatible": True}}}
        self.assertEqual(final_axes(rev, evaluation_record("accept"), proto, evaluation_error=None, revision_dir=rdir, evidence=self.EVIDENCE, final_meta=complete)["visual"]["status"],
                         "accepted")

    def test_every_tag_covered_gives_decide_status_answer(self):
        store = self.make_store()
        rev, rdir = self.revision(store)
        proto = self.protocol("pair", "rim_full", "translucent")
        axes = final_axes(rev, evaluation_record("accept"), proto, evaluation_error=None, revision_dir=rdir, evidence=self.EVIDENCE, final_meta=self.meta())
        self.assertEqual(axes["visual"]["uncovered_tags"], [])
        expected = mevaluate.decide_status(candidate_valid=True, metrics=self.SUMMARY, heldout=None, evaluation=evaluation_record("accept"), input_flags=[],
                                           protocol_calibrated=True, tags=["pair", "rim_full", "translucent"], uncovered_tags=[], gate_overrides={})
        self.assertEqual(axes["visual"]["status"], expected["status"])
        self.assertEqual(axes["visual"]["status"], "accepted")
        self.assertEqual(axes["visual"]["automatic_verdict"], "accept")
        self.assertTrue(axes["visual"]["wearer_evidence"]["complete"])
        # the same asset judged 'reject' by the evaluator stays a reject: coverage never upgrades
        self.assertEqual(final_axes(rev, evaluation_record("reject"), proto, evaluation_error=None, revision_dir=rdir, evidence=self.EVIDENCE,
                                    final_meta=self.meta())["visual"]["automatic_verdict"], "reject")

    def test_coverage_block_missing_from_a_calibrated_protocol_leaves_every_tag_uncovered(self):
        store = self.make_store()
        rev, rdir = self.revision(store)
        axes = final_axes(rev, evaluation_record("accept"), self.protocol(coverage=False), evaluation_error=None, revision_dir=rdir,
                          evidence=self.EVIDENCE, final_meta=self.meta())
        self.assertEqual(axes["visual"]["uncovered_tags"], ["pair", "rim_full", "translucent"])
        self.assertNotEqual(axes["visual"]["status"], "accepted")

    def test_wearer_render_count_zero_is_never_accepted_even_with_accept_and_covered_tags(self):
        store = self.make_store()
        rev, rdir = self.revision(store)
        proto = self.protocol("pair", "rim_full", "translucent")
        for meta in (self.meta(count=0), self.meta(count=0, error="RuntimeError: no node on this host"), self.meta(count=3, error="harness partial"),
                     {"request": "q0007"}, {"request": "q0007", "bindings": {}}):
            axes = final_axes(rev, evaluation_record("accept"), proto, evaluation_error=None, revision_dir=rdir, evidence=self.EVIDENCE, final_meta=meta)
            visual = axes["visual"]
            self.assertEqual(visual["automatic_verdict"], "unmeasured", meta)
            self.assertEqual(visual["status"], "quality_unverified", meta)
            self.assertFalse(visual["wearer_evidence"]["complete"])
            self.assertTrue(any("wearer" in r for r in visual["reasons"]), visual["reasons"])
            self.assertEqual(visual["evaluator_overall"], "accept", "what the evaluator said is recorded, not acted on")
            self.assertEqual(visual["tags"], ["pair", "rim_full", "translucent"])
        axes = final_axes(rev, evaluation_record("accept"), proto, evaluation_error=None, revision_dir=rdir, evidence=self.EVIDENCE,
                          final_meta=self.meta(count=0, error="RuntimeError: no node on this host"))
        self.assertTrue(any("no node on this host" in r for r in axes["visual"]["reasons"]), axes["visual"]["reasons"])

    def test_observation_without_a_valid_ar_report_is_unmeasured(self):
        store = self.make_store()
        proto = self.protocol("pair", "rim_full", "translucent")
        for missing in ("ar_report_valid", "ar_runtime_compatible"):
            summary = {k: v for k, v in self.SUMMARY.items() if k != missing}
            rev, rdir = self.revision(store, summary=summary)
            axes = final_axes(rev, evaluation_record("accept"), proto, evaluation_error=None, revision_dir=rdir, evidence=self.EVIDENCE, final_meta=self.meta())
            self.assertEqual(axes["visual"]["automatic_verdict"], "unmeasured", missing)
            self.assertEqual(axes["visual"]["status"], "quality_unverified", missing)
            self.assertTrue(any(missing in r for r in axes["visual"]["reasons"]), axes["visual"]["reasons"])
        rev, rdir = self.revision(store, summary=dict(self.SUMMARY, ar_report_valid=False))
        axes = final_axes(rev, evaluation_record("accept"), proto, evaluation_error=None, revision_dir=rdir, evidence=self.EVIDENCE, final_meta=self.meta())
        self.assertEqual(axes["visual"]["status"], "quality_unverified")

    def test_tags_fall_back_to_the_glb_and_to_kind_tags_when_the_build_record_is_missing(self):
        store = self.make_store()
        rev, rdir = self.revision(store, materials=None)          # no build/materials.json; GLB_BYTES is not a readable GLB
        axes = final_axes(rev, evaluation_record("accept"), self.protocol("pair", "rim_full"), evaluation_error=None, revision_dir=rdir,
                          evidence=self.EVIDENCE, final_meta=self.meta())
        self.assertEqual(axes["visual"]["tags"], ["pair", "rim_full"])
        self.assertEqual(axes["visual"]["status"], "accepted")
        # result.host.json naming the materials record is honoured when build/materials.json is absent
        (rdir / "build").mkdir(parents=True, exist_ok=True)
        (rdir / "build" / "mats-elsewhere.json").write_text(json.dumps(self.TRANSLUCENT), encoding="utf-8")
        (rdir / "build" / "result.host.json").write_text(json.dumps({"materials_json": str(rdir / "build" / "mats-elsewhere.json")}), encoding="utf-8")
        axes = final_axes(rev, evaluation_record("accept"), self.protocol("pair", "rim_full"), evaluation_error=None, revision_dir=rdir,
                          evidence=self.EVIDENCE, final_meta=self.meta())
        self.assertEqual(axes["visual"]["tags"], ["pair", "rim_full", "translucent"])
        self.assertEqual(axes["visual"]["uncovered_tags"], ["translucent"])
        self.assertNotEqual(axes["visual"]["status"], "accepted")

    def test_old_call_signature_still_works_and_honours_protocol_tags(self):
        store = self.make_store()
        rev, _rdir = self.revision(store)
        legacy = final_axes(rev, evaluation_record("accept"), {"visual_bar_calibrated": True, "input_flags": [], "gate_overrides": {}}, evaluation_error=None)
        self.assertEqual(legacy["visual"]["status"], "accepted", "an old caller without evidence kwargs keeps the pre-2026-09-28 answer")
        self.assertEqual(legacy["visual"]["tags"], [])
        self.assertIsNone(legacy["visual"]["wearer_evidence"]["complete"], "not checked: the caller passed no final_meta")
        tagged = final_axes(rev, evaluation_record("accept"), {"visual_bar_calibrated": True, "tags": ["translucent"], "uncovered_tags": ["translucent"]},
                            evaluation_error=None)
        self.assertEqual(tagged["visual"]["uncovered_tags"], ["translucent"])
        self.assertNotEqual(tagged["visual"]["status"], "accepted")
        heldout = {"summary": {"mean_contour_mm_all_fit_views": 1.2}}
        with_held = final_axes(rev, evaluation_record("accept"), {"visual_bar_calibrated": False}, evaluation_error=None, heldout=heldout)
        self.assertEqual(with_held["visual"]["provisional"]["heldout_contour_mean_mm"]["value"], 1.2)

    def test_final_blocks_reports_whether_the_wearer_evidence_is_complete(self):
        store = self.make_store()
        arts = ArtifactStore(store)
        self.reserve(store, arts)
        rev, rdir = self.revision(store)
        with mock.patch.object(evaluation.mevaluate, "wearer_renders", side_effect=RuntimeError("no node on this host")):
            blocks, bindings = final_blocks(store, arts, self.EVIDENCE, rev, self.protocol("pair"), revision_dir=rdir)
        self.assertFalse(bindings["evidence_complete"])
        self.assertTrue(any("no node on this host" in p for p in bindings["evidence_problems"]), bindings["evidence_problems"])
        self.assertEqual(bindings["wearer_renders"]["count"], 0)
        self.assertEqual(len(blocks), 1 + 2 * 4, "the message is still built: the runner decides whether to spend on it")
        renders = []
        for view in ("front", "angled", "rolled"):
            p = rdir / "observe" / "ar_wearer" / f"candidate__{view}.png"
            p.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (160, 120), mevaluate._hex_rgb(mevaluate.WEARER_BACKGROUND)).save(p)
            renders.append(str(p))
        with mock.patch.object(evaluation.mevaluate, "wearer_renders", return_value={"renders": renders}):
            _blocks, bindings = final_blocks(store, arts, self.EVIDENCE, rev, self.protocol("pair"), revision_dir=rdir)
        self.assertTrue(bindings["evidence_complete"])
        self.assertEqual(bindings["evidence_problems"], [])
        self.assertEqual(bindings["wearer_renders"]["count"], 3)
        # the same binding fed back through run_final's meta is what final_axes judges
        axes = final_axes(rev, evaluation_record("accept"), self.protocol("pair", "rim_full", "translucent"), evaluation_error=None, revision_dir=rdir,
                          evidence=self.EVIDENCE, final_meta={"bindings": bindings})
        self.assertEqual(axes["visual"]["status"], "accepted")


# =============================================================================== evaluator inputs (test-pilot-002 review, 2026-09-28)
TOMFORD_NOTES = ("Owner's test pair, photos only (catalog product photos, white background). What the photos show: round panto sunglasses in clear, "
                 "colourless crystal acetate; front, bridge, endpieces and temples are transparent so the lens edges, the hinge barrels and a thin gold "
                 "metal core wire inside each temple are visible. A flat gold 'T' inlay on the outer face of each temple at the hinge; a small gold oval "
                 "plaque on the outside near each temple tip. Lenses: flat, light brown flash-mirror with a gold sheen, a small 'TOM FORD' print in the "
                 "upper outer area of the right lens. Keyhole-free plain bridge, rounded lens shape, temples slightly bowed. No physical dimensions supplied.")
RUN2_CALIBRATION = {"calibrated": False, "disagreements": 4, "evaluator": "astra",
                    "coverage": {"mirrored": {"accepts": 4, "rejects": 0, "covered": False, "disagreements": 4}, "pair": {"accepts": 3, "rejects": 4, "covered": True},
                                 "single": {"accepts": 4, "rejects": 0, "covered": False, "disagreements": 4}},
                    "reasons": ["4 automatic verdicts disagree with the owner: oakley-astra1/c0007 owner accept vs automatic reject"]}


class EvaluatorInputsTests(_JobBase):
    """CE-8 (empty identity checklist), CE-9 (no glossary; the crystal side IoU of 0.44 passed unqualified), M8 (a bar that
    disagreed with the owner on 4 of 4 mirrored assets, silent in the input and the manifest), AT-08/F7 (critic cap) and
    the shared 'lens_backdrop' sheet."""
    PROTOCOL = {"identity_checklist": ["round panto front"], "checklist_source": "request_notes", "visual_bar_calibrated": False,
                "calibration_summary": RUN2_CALIBRATION, "gate_thresholds_mm": mevaluate.GATE_THRESHOLDS_MM, "input_flags": ["floor_reflection_cut", "mirror_iou_low"]}
    EVIDENCE = {"notes": "fixture notes", "views": {"front": {"view": "front", "flags": ["mirror_iou_low"]}, "left": {"view": "left", "flags": ["floor_reflection_cut"]},
                                                    "back": {"view": "back", "flags": []}}}

    def test_checklist_from_the_request_notes_is_a_deterministic_split_without_meta_clauses(self):
        items = evaluation.checklist_from_notes(TOMFORD_NOTES)
        self.assertEqual(items, evaluation.checklist_from_notes(TOMFORD_NOTES), "deterministic")
        self.assertEqual(items[0], "round panto sunglasses in clear, colourless crystal acetate")
        self.assertIn("A flat gold 'T' inlay on the outer face of each temple at the hinge", items)
        self.assertIn("a small gold oval plaque on the outside near each temple tip", items)
        self.assertTrue(any("'TOM FORD' print" in x for x in items))
        self.assertTrue(any(x.startswith("Keyhole-free plain bridge") for x in items))
        self.assertEqual(len(items), 6, items)
        for meta in ("Owner's test pair", "No physical dimensions", "What the photos show"):
            self.assertFalse(any(meta in x for x in items), meta)
        self.assertEqual(evaluation.checklist_from_notes(""), [])
        self.assertLessEqual(len(evaluation.checklist_from_notes("; ".join(f"feature number {i}" for i in range(40)))), evaluation.MAX_CHECKLIST_ITEMS)

    def test_final_message_carries_the_glossary_calibration_reliability_and_view_reliability(self):
        store = self.make_store()
        arts = ArtifactStore(store)
        self.reserve(store, arts)
        summary = {"front_contour_mean_mm": 1.27, "lens_outline_mean_mm": 1.97, "ar_runtime_compatible": True, "ar_report_valid": True,
                   "lens_colour": {"saturation_ratio": 1.05, "value_ratio": 0.92, "hue_error": 0.002}}
        views = {"front": {"view": "front", "iou": 0.784, "contour_mean_mm": 1.27, "contour_p95_mm": 3.69},
                 "left": {"view": "left", "iou": 0.4414, "contour_mean_mm": 1.48, "contour_p95_mm": 3.83, "reliable": False, "reason": "matte coverage 0.41"},
                 "back": {"view": "back", "iou": 0.78, "contour_mean_mm": 3.26, "contour_p95_mm": 22.8}}
        rev = self.add_revision(store, synthetic=False, compatible=True, glb_sha256=GLB_SHA, summary=summary, views=views)
        backdrop = self.add_image(arts, rev, kind="sheet", role=AUTHOR_VISIBLE, view="lens_backdrop")
        match = self.add_image(arts, rev, kind="sheet", role=AUTHOR_VISIBLE, view="photo_match")
        blocks, bindings = final_blocks(store, arts, self.EVIDENCE, rev, self.PROTOCOL, revision_dir=self.rev_dir(store, rev))
        request = json.loads(texts(blocks)[0])
        glossary = request["measurement_glossary"]
        for key in ("iou", "contour_mean_mm", "lens_outline_mean_mm", "lens_colour", "frame_see_through", "reliable"):
            self.assertIn(key, glossary)
        self.assertIn("prediction / photo", glossary["lens_colour"])
        self.assertEqual(glossary, evaluation.MEASUREMENT_GLOSSARY)
        cal = request["calibration_reliability"]
        self.assertEqual(cal["visual_bar_calibrated"], False)
        self.assertEqual(cal["owner_disagreements"], 4)
        self.assertEqual(cal["uncovered_tags"], ["mirrored", "single"])
        self.assertNotIn("oakley", json.dumps(cal), "no per-asset calibration labels reach the evaluator")
        rel = request["measurement_reliability"]
        self.assertIn("floor_reflection_cut", rel["left"]["flags"])
        self.assertEqual(rel["left"]["iou"], "report_only")
        self.assertIn("matte coverage 0.41", " ".join(rel["left"]["reasons"]))
        self.assertIn("mirror_iou_low", rel["front"]["flags"])
        self.assertNotIn("back", rel, "a view without a matte flag or a reliable:false metric needs no qualification")
        # the shared-interface fields ride along per view when the observer supplies them
        self.assertEqual(request["measurements"]["per_view"]["left"]["reliable"], False)
        self.assertNotIn("reliable", request["measurements"]["per_view"]["front"])
        text = "\n".join(texts(blocks))
        self.assertIn(backdrop["id"], text)
        self.assertIn(match["id"], text)
        self.assertLess(text.index(match["id"]), text.index(backdrop["id"]))
        self.assertIn(backdrop["id"], {i["id"] for i in bindings["images"]})
        low = text.lower()
        for forbidden in ("rationale", "expected", "owner accepted"):
            self.assertNotIn(forbidden, low, forbidden)

    def test_critic_gets_the_glossary_and_a_bounded_concise_schema(self):
        store = self.make_store()
        arts = ArtifactStore(store)
        self.reserve(store, arts)
        rev = self.add_revision(store, synthetic=False, compatible=True, glb=GLB_BYTES, summary={"lens_colour": {"value_ratio": 0.88}})
        backdrop = self.add_image(arts, rev, kind="sheet", role=AUTHOR_VISIBLE, view="lens_backdrop")
        blocks = critic_blocks(store, arts, self.EVIDENCE, rev, "lens colour?")
        head = json.loads(texts(blocks)[0])
        self.assertEqual(head["measurement_glossary"], evaluation.MEASUREMENT_GLOSSARY)
        self.assertIn(backdrop["id"], "\n".join(texts(blocks)))
        schema = evaluation.CRITIC_TOOL["report_critique"]["parameters"]["properties"]
        self.assertLessEqual(schema["defects"]["maxItems"], 8)
        self.assertLessEqual(schema["defects"]["items"]["properties"]["description"]["maxLength"], 500)
        self.assertIn("concise", evaluation.CRITIC_TASK.lower())

    def test_manifest_and_report_state_the_calibration_reliability(self):
        store = self.make_store()
        arts = ArtifactStore(store)
        self.reserve(store, arts)
        store.set_setting("protocol", self.PROTOCOL)
        rev = self.add_revision(store, synthetic=False, compatible=True, glb=GLB_BYTES)
        axes = final_axes(rev, evaluation_record("reject"), self.PROTOCOL, evaluation_error=None)
        manifest = self.deliver(store, arts, rev, axes=axes)
        cal = manifest["calibration"]
        self.assertEqual((cal["visual_bar_calibrated"], cal["owner_disagreements"], cal["uncovered_tags"]), (False, 4, ["mirrored", "single"]))
        self.assertIn("not calibrated", cal["statement"])
        report = (store.job_dir / "deliverable" / "report.md").read_text(encoding="utf-8")
        head = report.split("## Verdict axes", 1)[0]
        self.assertIn("not calibrated", head, "the calibration statement comes before the verdicts")
        self.assertIn("4 disagreement", head)
        self.assertIn("| automatic visual verdict |", report)
        self.assertNotIn("{'", report, "no raw Python dict in the report")
