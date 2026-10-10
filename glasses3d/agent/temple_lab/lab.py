"""Local temple laboratory: bounded assets, immutable revisions, real Blender scene."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
from pathlib import Path
import re
import uuid

import numpy as np
from PIL import Image, ImageDraw

from glasses3d.agent.journal import BlenderSession
from glasses3d.agent.loop import SceneHooks
from glasses3d.agent.seeing import Seeing
from glasses3d.agent.procs import write_json_atomic
from glasses3d.g3d.temple import build_temple
from glasses3d.g3d.temple.spec import TempleError, canonical, plain
from glasses3d.g3d_host import fitmesh
from .geometry import blank_template, chart


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def data_url(path):
    path = Path(path)
    mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".png": "image/png"}[path.suffix.lower()]
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def named(name):
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,39}", name):
        raise ValueError("IDs must start with a letter and contain up to 40 letters/digits/_/-")
    return name


class FixedSeeing(Seeing):
    """Use the start bounds for cameras AND light rig, regardless of later geometry."""
    def __init__(self, lab):
        super().__init__(lab.s)
        self.lab = lab

    def bounds(self, call_id):
        fixed = self.lab.state.get("view_bounds")
        return copy.deepcopy(fixed) if fixed else super().bounds(call_id)


class Lab(SceneHooks):
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.manifest = read(self.root / "inputs" / "lab.json")
        self.s = BlenderSession(self.root, seed_blend=self.root / "inputs" / "scene.blend",
                                calls_dir=self.root / "lab_calls")
        self.seeing = FixedSeeing(self)
        self.work = self.root / "work" / "temple_lab"
        self.work.mkdir(parents=True, exist_ok=True)
        self.state_path = self.work / "state.json"
        self.state = read(self.state_path) if self.state_path.exists() else {
            "images": {}, "marks": {}, "candidates": [], "renders": {}, "applied": {}, "events": []}

    def save(self):
        write_json_atomic(self.state_path, self.state)

    def verify_inputs(self):
        for row in self.manifest["assets"]:
            p = (self.root / "inputs" / row["path"]).resolve()
            if not p.is_relative_to(self.root / "inputs") or sha(p) != row["sha256"]:
                raise ValueError("input bytes changed or escaped the package")

    def start(self):
        self.verify_inputs()
        result = self.s.start()
        if "start_checkpoint" not in self.state:
            self.state["start_checkpoint"] = self.s.head["id"]
            self.state["start_fingerprint"] = self.s.head["fingerprint"]
            for row in self.manifest["photos"]:
                self.register_image(row["id"], self.root / "inputs" / row["path"], kind="photo")
            export = self.seeing.export_head()
            if export["status"] != "exported":
                raise ValueError(f"original model export failed: {export}")
            mesh = fitmesh.load_fit_mesh(export["path"])
            # Identify whole node, retaining all material primitives, not just its first primitive.
            choices = []
            for name in set(mesh.node_names):
                ids = [i for i, n in enumerate(mesh.node_names) if n == name]
                faces = mesh.F[np.isin(mesh.part, ids)]
                v = mesh.V[np.unique(faces)]
                if v[:, 0].min() > 20 and np.ptp(v[:, 2]) > 60 and "temple" in name.lower():
                    choices.append((float(np.ptp(v[:, 2])), name, v, faces))
            if not choices:
                raise ValueError("no +X temple for a blank template")
            _, name, v, faces = max(choices, key=lambda c: c[0])
            spec, diag = blank_template(v, np.searchsorted(np.unique(faces), faces))
            self.state["template"] = spec
            self.state["template_measurements"] = {"source_node": name, "export_sha256": export["sha256"], **diag}
            self.state["original_export"] = export
            b = self.seeing.bounds("lab-start-bounds")
            self.state["view_bounds"] = b
            lo, hi = np.asarray(b["lo"]), np.asarray(b["hi"])
            target = ((lo+hi)/2).tolist(); width = float(np.linalg.norm(hi-lo)*1.1)
            temple_center = ((v.min(0)+v.max(0))/2).tolist()
            self.state["views"] = {
                "full": dict(name="full", yaw=35, pitch=20, target_mm=target, width_mm=width),
                "side": dict(name="side", yaw=90, pitch=0, target_mm=temple_center, width_mm=diag["length_mm"]*1.15),
                "oblique": dict(name="oblique", yaw=55, pitch=18, target_mm=temple_center, width_mm=diag["length_mm"]*1.15),
                "other-side": dict(name="other-side", yaw=-90, pitch=0, target_mm=[-temple_center[0], *temple_center[1:]], width_mm=diag["length_mm"]*1.15),
                "top": dict(name="top", yaw=0, pitch=90, target_mm=target, width_mm=width),
                "end": dict(name="end", yaw=180, pitch=0, target_mm=target, width_mm=width),
            }
            self.save()
        return result

    def close(self):
        self.s.close()

    def worker_info(self):
        return {"worker_pid": self.s.worker.pid if self.s.worker else None}

    def journal_entries(self):
        return self.state["events"]

    def exit_checkpoint(self):
        head = self.s.head
        p = self.s.ck_dir / head["file"]
        return {"id": head["id"], "sha256": head["sha256"],
                "verified": sha(p) == head["sha256"] and self.s.fingerprint() == head["fingerprint"]}

    def restore(self, checkpoint_id, *, reason):
        return self.restore_scene(checkpoint_id, reason)

    def register_image(self, iid, path, *, kind, **meta):
        path = Path(path).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("image outside lab")
        with Image.open(path) as im:
            size = list(im.size)
        row = {"id": iid, "path": str(path.relative_to(self.root)), "sha256": sha(path), "size_px": size,
               "kind": kind, **meta}
        self.state["images"][iid] = row
        return row

    def image_path(self, iid):
        row = self.state["images"][iid]
        p = (self.root / row["path"]).resolve()
        if not p.is_relative_to(self.root) or sha(p) != row["sha256"]:
            raise ValueError("image bytes changed or escaped lab")
        return p

    def image(self, iid, crop=None):
        row = self.state["images"][iid]
        p = self.image_path(iid)
        if crop is None:
            return {"status": "ok", "image": row, "image_ids": [iid]}
        if len(crop) != 4 or any(type(x) is not int for x in crop):
            raise ValueError("crop is [left,top,right,bottom] integer pixels")
        l, t, r, b = crop; w, h = row["size_px"]
        if not (0 <= l < r <= w and 0 <= t < b <= h) or row["kind"] != "photo":
            raise ValueError("crop must be inside an original photo")
        cid = "crop-" + hashlib.sha256(json.dumps([iid, crop]).encode()).hexdigest()[:12]
        dest = self.work / f"{cid}.png"
        with Image.open(p) as im:
            im.crop(crop).save(dest)
        item = self.register_image(cid, dest, kind="crop", source=iid, source_rect=crop,
                                   transform="source_x=crop_x+left; source_y=crop_y+top; no resizing")
        self.save()
        return {"status": "ok", "image": item, "image_ids": [cid]}

    def inspect(self):
        spec = self.state["template"]
        path = self.work / "blank-chart.png"
        transform = chart(spec, path)
        self.register_image("blank-chart", path, kind="chart", transform=transform)
        self.save()
        return {"status": "ok", "checkpoint": self.s.head["id"], "start_checkpoint": self.state["start_checkpoint"],
                "candidate_revision": len(self.state["candidates"]), "template": spec,
                "current_candidate": self.state["candidates"][-1] if self.state["candidates"] else None,
                "applied_revision": self.state["applied"].get(self.s.head["fingerprint"]),
                "template_measurements": self.state["template_measurements"], "image_ids": ["blank-chart"],
                "photos": self.manifest["photos"], "marks": self.state["marks"],
                "available_views": list(self.state["views"]), "checkpoints": self.s.list_checkpoints(),
                "quality_status": "Experimental tool test. WP6 remains failed; no thresholds are changed."}

    def mark(self, iid, parent_revision, marks):
        row = self.state["images"][iid]
        if row["kind"] not in ("photo", "crop"):
            raise ValueError("mark only a photo or photo crop")
        old = self.state["marks"].get(iid, {"revision": 0, "marks": []})
        if parent_revision != old["revision"]:
            raise ValueError(f"stale mark revision; current={old['revision']}")
        if len(marks) > 32 or len({named(m["id"]) for m in marks}) != len(marks):
            raise ValueError("at most 32 uniquely named segments")
        for m in marks:
            pts = np.asarray(m["points"], float); w, h = row["size_px"]
            if pts.shape != (2, 2) or not np.isfinite(pts).all() or np.linalg.norm(pts[1]-pts[0]) < 2 or \
                    (pts < 0).any() or (pts[:, 0] >= w).any() or (pts[:, 1] >= h).any():
                raise ValueError("each mark needs two finite endpoints inside the image, at least 2px apart")
            if m["kind"] not in ("crease", "silhouette", "curvature", "uncertain"):
                raise ValueError("unsupported line classification")
        revision = old["revision"]+1
        item = {"revision": revision, "marks": copy.deepcopy(marks)}
        mid = f"marks-{iid}-{revision}"
        path = self.work / f"{mid}.png"
        im = Image.open(self.image_path(iid)).convert("RGB"); d = ImageDraw.Draw(im)
        for m in marks:
            pts = [tuple(p) for p in m["points"]]
            d.line(pts, fill="red", width=3)
            for n, (x, y) in enumerate(pts):
                d.ellipse((x-4, y-4, x+4, y+4), fill="yellow", outline="black")
                d.text((x+7, y+4), f"{m['id']}:{n}", fill="red", stroke_width=1, stroke_fill="white")
        im.save(path)
        self.state["marks"][iid] = item
        self.register_image(mid, path, kind="annotation", source=iid, revision=revision)
        write_json_atomic(self.work / f"{mid}.json", item)
        self.save()
        return {"status": "ok", "image": iid, **item, "image_ids": [mid],
                "note": "Full replacement for this image; omitted marks were deleted. Geometry is unchanged."}

    def refine(self, iid, revision, mark_id):
        from glasses3d.g3d_host.relief_trace import refine_line
        from glasses3d.g3d_host.relief_check import lightness
        item = self.state["marks"][iid]
        if revision != item["revision"]:
            raise ValueError("stale marking revision")
        mark = next(m for m in item["marks"] if m["id"] == mark_id)
        im = Image.open(self.image_path(iid)).convert("RGB")
        proposal = refine_line(lightness(np.asarray(im)), *mark["points"])
        result = {"status": "proposal" if proposal is not None else "no_edge", "image": iid, "mark": mark,
                  "proposal": plain(proposal), "accepted": False,
                  "note": "Contrast refinement only. It may select a reflection. Use lab_mark explicitly to accept."}
        if proposal is not None:
            d = ImageDraw.Draw(im)
            d.line([tuple(p) for p in mark["points"]], fill="red", width=3)
            d.line([tuple(proposal[k]) for k in ("p0", "p1")], fill="cyan", width=3)
            rid = f"refine-{iid}-{revision}-{mark_id}"
            path = self.work / f"{rid}.png"; im.save(path)
            self.register_image(rid, path, kind="refinement", source=iid)
            result["image_ids"] = [rid]; self.save()
        return result

    def candidate(self, parent_revision, nodes, facets, evidence, note):
        if parent_revision != len(self.state["candidates"]):
            raise ValueError("stale candidate revision")
        if not 1 <= len(nodes) <= 64 or not 1 <= len(facets) <= 32:
            raise ValueError("candidate needs 1..64 nodes and 1..32 facets")
        if len({named(n['id']) for n in nodes}) != len(nodes) or len({named(f['id']) for f in facets}) != len(facets):
            raise ValueError("duplicate node/facet ID")
        if any((f["crease"] is None) != (f["rise_per_mm"] is None) for f in facets):
            raise ValueError("crease and rise must either both be null or both be supplied")
        for ev in evidence:
            iid, rev, mid = ev.split(":")
            marks = self.state["marks"][iid]
            if int(rev) != marks["revision"] or mid not in {m["id"] for m in marks["marks"]}:
                raise ValueError("evidence must name current image:revision:mark")
        if not evidence or not note.strip():
            raise ValueError("candidate needs marking evidence and a rationale")
        spec = copy.deepcopy(self.state["candidates"][-1]["spec"] if self.state["candidates"] else self.state["template"])
        spec["nodes"] = {n["id"]: [n["d_mm"], n["y_mm"], n["relief_mm"]] for n in nodes}
        spec["facets"] = {f["id"]: {"poly": f["nodes"], "plane": "nodes" if f["crease"] is None else
                                  {"crease": f["crease"], "rise_per_mm": f["rise_per_mm"]}} for f in facets}
        return self._candidate(spec, evidence, note)

    def profile(self, parent_revision, top, bottom, inner, thickness, zone_end_d, note):
        if parent_revision != len(self.state["candidates"]) or not note.strip():
            raise ValueError("profile requires current revision and explanation")
        if any(not 2 <= len(chain) <= 160 for chain in (top, bottom, inner, thickness)):
            raise ValueError("each profile chain needs 2..160 [d,value] pairs")
        old = self.state["candidates"][-1] if self.state["candidates"] else None
        spec = copy.deepcopy(old["spec"] if old else self.state["template"])
        spec["outline"]["top"], spec["outline"]["bottom"] = top, bottom
        spec["inner"], spec["thickness"], spec["zone_end_d"] = inner, thickness, zone_end_d
        return self._candidate(spec, old["evidence"] if old else [], note)

    def map_marks(self, iid, revision, anchors):
        marks = self.state["marks"][iid]
        if marks["revision"] != revision or not 3 <= len(anchors) <= 12:
            raise ValueError("mapping requires current markings and 3..12 corresponding anchors")
        p = np.asarray([a["pixel"] for a in anchors], float)
        q = np.asarray([a["chart_mm"] for a in anchors], float)
        w, h = self.state["images"][iid]["size_px"]
        if p.shape != (len(anchors), 2) or q.shape != p.shape or not np.isfinite(np.r_[p,q]).all() or \
                (p < 0).any() or (p[:, 0] >= w).any() or (p[:, 1] >= h).any():
            raise ValueError("anchor pixels must be inside this image; chart anchors are finite [d,y] mm")
        center = p.mean(0); scale = float(np.linalg.norm(p-center, axis=1).max())
        if scale < 2:
            raise ValueError("anchors too close")
        a = np.c_[(p-center)/scale, np.ones(len(p))]
        if np.linalg.cond(a) > 100:
            raise ValueError("anchors nearly collinear; choose spread-out correspondences")
        matrix = np.linalg.lstsq(a, q, rcond=None)[0]
        out = {m["id"]: (np.c_[(np.asarray(m["points"])-center)/scale, np.ones(2)]@matrix).tolist()
               for m in marks["marks"]}
        return {"status": "proposal", "source": iid, "revision": revision, "chart_segments_mm": out,
                "anchor_residual_mm": np.linalg.norm(a@matrix-q, axis=1).tolist(), "anchors": anchors,
                "normalization": {"pixel_center": center.tolist(), "pixel_scale": scale}, "matrix": matrix.tolist(),
                "warning": "Approximate side-plane mapping from YOUR correspondences. Zero residual with 3 anchors "
                           "does not validate geometry. Perspective, curved surfaces and depth cause parallax. "
                           "No relief was recovered and no candidate was changed."}

    def _candidate(self, spec, evidence, note):
        spec = canonical(spec)
        # Engineering resource bounds for this lab, not amended WP6 acceptance thresholds.
        for chain in (spec["outline"]["top"], spec["outline"]["bottom"], spec["inner"], spec["thickness"]):
            if any(not 0 <= p[0] <= 250 or abs(p[1]) > 200 for p in chain):
                raise ValueError("lab profile bounds: d 0..250 mm, absolute values <=200 mm")
        if any(abs(p[0]) > 300 or abs(p[1]) > 200 or (p[2] is not None and abs(p[2]) > 20)
               for p in spec["nodes"].values()):
            raise ValueError("lab node bounds: |d|<=300, |y|<=200, |relief|<=20 mm")
        tb = build_temple(spec)  # No scene mutation if any structural validation refuses.
        revision = len(self.state["candidates"])+1
        path = self.work / f"candidate-{revision}.json"
        write_json_atomic(path, spec)
        cp = self.work / f"candidate-{revision}.png"
        transform = chart(spec, cp)
        iid = f"candidate-{revision}"
        self.register_image(iid, cp, kind="chart", transform=transform)
        rec = {"revision": revision, "spec": spec, "sha256": sha(path), "evidence": evidence, "note": note}
        self.state["candidates"].append(rec); self.save()
        return {"status": "validated", **rec, "image_ids": [iid],
                "note_tool": "Structural validity only. Not applied yet; no depth optimizer ran.",
                "geometry": plain({"checks": tb.checks, "tables": tb.tables, "warnings": tb.warnings})}

    def depth(self, parent_revision, changes, rises, note):
        if parent_revision != len(self.state["candidates"]) or parent_revision == 0:
            raise ValueError("depth requires the current nonzero candidate revision")
        old = self.state["candidates"][-1]
        spec = copy.deepcopy(old["spec"])
        if len({x["node"] for x in changes}) != len(changes) or len({x["facet"] for x in rises}) != len(rises):
            raise ValueError("duplicate depth edit")
        for x in changes:
            spec["nodes"][x["node"]][2] = x["relief_mm"]
        for x in rises:
            p = spec["facets"][x["facet"]]["plane"]
            if not isinstance(p, dict):
                raise ValueError("rise edits require a crease-plane facet")
            p["rise_per_mm"] = x["rise_per_mm"]
        if not (changes or rises) or not note.strip():
            raise ValueError("depth edit needs a change and rationale")
        return self._candidate(spec, old["evidence"], note)

    def apply(self, revision, checkpoint, call_id):
        if revision != len(self.state["candidates"]) or revision < 1 or checkpoint != self.s.head["id"]:
            raise ValueError("apply requires the current candidate and scene checkpoint")
        self.verify_inputs()
        before = self.s.head["id"]
        # Rebuild from original each time so repeated decal projections cannot accumulate.
        reset = self.s.restore_checkpoint(self.state["start_checkpoint"], call_id=call_id+"-base")
        if reset["status"] != "ok":
            return reset
        try:
            out = self.s.call_mutating("lab_apply", "temple_lab_build",
                                      {"spec": self.state["candidates"][-1]["spec"]}, deadline_s=300,
                                      call_id=call_id, label=f"TOOL TEST candidate {revision}")
        except Exception:
            # A fatal worker exception must not strand the scene on the temporary original reset.
            # If recovery itself fails, its chained exception is fatal too; both checkpoints remain on disk.
            self.s.restore_checkpoint(before, call_id=call_id+"-exception-rollback")
            raise
        if out["status"] != "ok":
            out["rollback"] = self.s.restore_checkpoint(before, call_id=call_id+"-rollback")
        else:
            self.state["applied"][self.s.head["fingerprint"]] = revision
        self.state["events"].append({"call_id": call_id, "tool": "lab_apply", "mutating": True,
                                     "checkpoint_before": before, "checkpoint_after": self.s.head["id"]})
        self.save()
        if "result" in out:
            full = out["result"]
            # Full native receipt stays in lab_calls; send useful measurements and every warning, not tessellation tables.
            out["result"] = {k:full[k] for k in ("status", "code", "message", "fields", "error", "spec_sha256",
                                                "facets", "creases", "thickness", "geometry", "checks", "warnings", "sides")
                             if k in full}
        return {**out, "candidate_revision": revision, "scene_checkpoint": self.s.head["id"],
                "warning": "Native construction receipt is not a WP6 quality pass. Inspect decals and all views."}

    def render(self, views, passes):
        if not 1 <= len(views) <= 6 or len(set(views)) != len(views):
            raise ValueError("choose 1..6 distinct fixed views")
        result = self.seeing.render_views([self.state["views"][v] for v in views], passes=passes,
                                         size_px=768, background="grey", samples=16,
                                         call_id="lab-render-"+uuid.uuid4().hex)
        if result["status"] != "ok":
            return result
        rid = result["call_id"]
        ids = []
        for v, rec in result["views"].items():
            for ps, path in rec["files"].items():
                iid = f"{rid}-{v}-{ps}"
                im = Image.open(path).convert("RGBA")
                bg = Image.new("RGBA", im.size, (185, 185, 185, 255)); bg.alpha_composite(im)
                visible = self.work / f"{iid}.png"; bg.convert("RGB").save(visible)
                self.register_image(iid, visible, kind="render", render_id=rid, view=v, pass_name=ps,
                                    checkpoint=self.s.head["id"])
                ids.append(iid)
        self.state["renders"][rid] = {"manifest": result["manifest"], "checkpoint": self.s.head["id"]}
        self.save()
        return {"status": "ok", "render_id": rid, "checkpoint": self.s.head["id"], "image_ids": ids,
                "views": result["views"], "candidate_revision": self.state["applied"].get(self.s.head["fingerprint"]),
                "fixed_views": {v: self.state["views"][v] for v in views}}

    def pick(self, rid, view, points):
        rec = self.state["renders"][rid]
        if rec["checkpoint"] != self.s.head["id"]:
            raise ValueError("render is stale for this scene; render again before picking")
        out = self.seeing.pick_surface_point(view, points, manifest=rec["manifest"], size_px=768)
        out["warning"] = "Points on CURRENT MODEL only, not photographic depth measurements."
        out["hinge_z_mm"] = self.state["template"]["hinge"]["z_mm"]
        return out

    def restore_scene(self, checkpoint, reason, call_id="lab-restore"):
        before = self.s.head["id"]
        out = self.s.restore_checkpoint(checkpoint, call_id=call_id)
        self.state["events"].append({"call_id": call_id, "tool": "lab_restore", "mutating": True,
                                     "checkpoint_before": before, "checkpoint_after": self.s.head["id"], "reason": reason})
        self.save()
        return out

    def export(self, checkpoint):
        if checkpoint != self.s.head["id"]:
            raise ValueError("export requires current checkpoint")
        self.verify_inputs()
        return {**self.seeing.export_head(), "label": "EXPERIMENT / TOOL TEST — not accepted production model",
                "candidate_revision": self.state["applied"].get(self.s.head["fingerprint"])}
