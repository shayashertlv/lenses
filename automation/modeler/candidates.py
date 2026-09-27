"""Candidate store: every construction program set, its build, export, observation and score, kept forever.

A candidate is a set of program MODULES (executed in a fixed order). A new candidate may inherit modules from a
base candidate, so a targeted repair rewrites one module and keeps the rest byte-identical. Builds run on a fresh
Blender process into the candidate's own folder: a failed edit never touches the incumbent.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import time

from . import export as mexport
from .observe import canonical_render_specs
from .worker import run_harness

MODULE_ORDER = ("setup", "frame", "lenses", "temples", "hardware", "materials", "finish")
MAX_MODULE_BYTES = 60_000
SCORE_WEIGHTS = {"front": 0.45, "back": 0.15, "left": 0.10, "right": 0.10, "lens_outline": 0.20}
SCORE_NOISE = 0.01          # relative camera-refit noise of the silhouette score (identical geometry re-observed: ~0.6 %)


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


class Candidate:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.id = self.root.name
        self.program_dir = self.root / "program"
        self.record = json.loads((self.root / "program.json").read_text(encoding="utf-8")) if (self.root / "program.json").exists() else {}

    # ---- persisted pieces
    def _load(self, rel: str) -> dict | None:
        p = self.root / rel
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    @property
    def build(self) -> dict | None:
        return self._load("build/result.json")

    @property
    def export(self) -> dict | None:
        return self._load("export.json")

    @property
    def observation(self) -> dict | None:
        return self._load("observe/observation.json")

    @property
    def heldout(self) -> dict | None:
        return self._load("heldout/heldout.json")

    @property
    def glb(self) -> Path | None:
        p = self.root / "model.glb"
        return p if p.is_file() else None

    def modules(self) -> dict[str, str]:
        return {name: (self.program_dir / f"{name}.py").read_text(encoding="utf-8") for name in self.record.get("module_order", [])}

    def status(self) -> str:
        b = self.build
        if b is None:
            return "unbuilt"
        if not b.get("ok"):
            return "build_failed"
        e = self.export
        if e is None:
            return "built"
        if e.get("error") or not (e.get("contract") or {}).get("ok"):
            return "export_failed"
        o = self.observation
        if o is None:
            return "exported"
        return "observed"

    def valid(self) -> bool:
        """Contract ok and loaded in the AR renderer with at least one optical mesh."""
        e, o = self.export, self.observation
        if not e or not (e.get("contract") or {}).get("ok"):
            return False
        if not o or not o.get("ar"):
            return False
        m = o["ar"].get("models", {}).get("candidate", {})
        # the runtime's temple continuity check is a gate of the visual bar (both BSA rejects failed it): a candidate
        # whose temples the runtime cuts short is not a valid deliverable
        return bool(m.get("runtime_compatible")) and (m.get("optical_meshes_detected") or 0) >= 1 and not m.get("continuity_failure")

    def appearance_penalty(self) -> float:
        """Lens colour distance from the photo (0 = same): hue error, log saturation ratio, log value ratio, from
        summary.lens_colour; 0 when not measured. Used only to break silhouette ties (same geometry, new materials)."""
        lc = ((self.observation or {}).get("summary") or {}).get("lens_colour") or {}
        if not lc:
            return 0.0
        pen = float(lc.get("hue_error") or 0.0)
        for key in ("saturation_ratio", "value_ratio"):
            v = lc.get(key)
            if v:
                pen += 0.5 * abs(math.log(max(float(v), 1e-3)))
        return pen

    def score(self) -> float | None:
        """Lower is better: weighted silhouette contour error (mm) over the fit views plus the lens outline error.
        None when the candidate is not valid or no fit view was measured."""
        if not self.valid():
            return None
        s = (self.observation or {}).get("summary", {})
        terms, wsum = 0.0, 0.0
        views = (self.observation or {}).get("views", {})
        for vid, v in views.items():
            if "contour_mean_mm" not in v:
                continue
            w = SCORE_WEIGHTS.get(v["view"], 0.1)
            terms += w * v["contour_mean_mm"]
            wsum += w
        if "lens_outline_mean_mm" in s:
            terms += SCORE_WEIGHTS["lens_outline"] * s["lens_outline_mean_mm"]
            wsum += SCORE_WEIGHTS["lens_outline"]
        if wsum == 0:
            return None
        return round(terms / wsum, 4)

    def summary(self) -> dict:
        out = {"id": self.id, "status": self.status(), "valid": self.valid(), "score_mm": self.score(),
               "turn": self.record.get("turn"), "base": self.record.get("base"), "modules": self.record.get("module_order"),
               "changed_modules": self.record.get("changed_modules")}
        b = self.build
        if b:
            out["build_ok"] = b.get("ok")
            errs = [m for m in b.get("module_results", []) if not m.get("ok")]
            if errs:
                out["build_error"] = {"module": errs[0]["name"], "error": errs[0]["error"]}
            if b.get("error"):
                out["build_error"] = {"module": None, "error": b["error"]}
        e = self.export
        if e:
            out["contract_ok"] = (e.get("contract") or {}).get("ok")
            out["contract_failures"] = (e.get("contract") or {}).get("failures")
            out["glb_sha256"] = e.get("sha256")
            out["triangles"] = (e.get("receipt") or {}).get("triangles")
            out["export_error"] = e.get("error")
        o = self.observation
        if o:
            out["metrics"] = o.get("summary")
        return out


class CandidateStore:
    def __init__(self, job_dir: Path):
        self.root = Path(job_dir) / "candidates"
        self.root.mkdir(parents=True, exist_ok=True)

    def ids(self) -> list[str]:
        return sorted(p.name for p in self.root.iterdir() if p.is_dir() and p.name.startswith("c"))

    def get(self, cid: str) -> Candidate:
        p = self.root / cid
        if not p.is_dir():
            raise KeyError(cid)
        return Candidate(p)

    def all(self) -> list[Candidate]:
        return [Candidate(self.root / c) for c in self.ids()]

    def create(self, modules: dict[str, str], *, base: Candidate | None, turn: int, rationale: str,
               author_ref: str) -> Candidate:
        """A new candidate from submitted modules plus the base's other modules (inherited byte-identical)."""
        for name, src in modules.items():
            if name not in MODULE_ORDER:
                raise ValueError(f"unknown module {name!r}; allowed: {MODULE_ORDER}")
            if not isinstance(src, str) or not src.strip():
                raise ValueError(f"module {name!r} is empty")
            if len(src.encode("utf-8")) > MAX_MODULE_BYTES:
                raise ValueError(f"module {name!r} exceeds {MAX_MODULE_BYTES} bytes")
        inherited = {}
        if base is not None:
            for name, src in base.modules().items():
                if name not in modules:
                    inherited[name] = (src, base.id)
        order = [m for m in MODULE_ORDER if m in modules or m in inherited]
        if not order:
            raise ValueError("a candidate needs at least one module")
        cid = f"c{len(self.ids()):04d}"
        root = self.root / cid
        (root / "program").mkdir(parents=True)
        record = {"id": cid, "turn": turn, "base": base.id if base else None, "rationale": rationale, "author_ref": author_ref,
                  "created": time.strftime("%Y-%m-%dT%H:%M:%S"), "module_order": order, "modules": {},
                  "changed_modules": sorted(modules), "inherited_modules": {n: inherited[n][1] for n in inherited}}
        for name in order:
            src = modules[name] if name in modules else inherited[name][0]
            (root / "program" / f"{name}.py").write_text(src, encoding="utf-8")
            record["modules"][name] = {"sha256": sha256_text(src), "bytes": len(src.encode("utf-8")),
                                       "source": "submitted" if name in modules else f"inherited:{inherited[name][1]}"}
        record["program_set_sha256"] = sha256_text(json.dumps({n: record["modules"][n]["sha256"] for n in order}, sort_keys=True))
        (root / "program.json").write_text(json.dumps(record, indent=1), encoding="utf-8")
        return Candidate(root)

    def incumbent(self) -> Candidate | None:
        """The strongest observed candidate: lowest silhouette score; scores within the camera-refit noise (1 %) count
        as equal and are broken by the lens colour distance, then by age (earliest). Until 2026-09-27 a materials-only
        repair with identical geometry could lose the incumbency to refit noise."""
        best = None
        for c in self.all():
            s = c.score()
            if s is None:
                continue
            if best is None:
                best = c
                continue
            b = best.score()
            if s < b * (1 - SCORE_NOISE):
                best = c
            elif s <= b * (1 + SCORE_NOISE) and c.appearance_penalty() < best.appearance_penalty() - 1e-9:
                best = c
        return best


# --------------------------------------------------------------------------- build / export
def build_candidate(cand: Candidate, evidence_path: Path | None, *, time_limit_s: int, canonical_renders: bool = False) -> dict:
    job = {"modules": [{"name": n, "path": str(cand.program_dir / f"{n}.py")} for n in cand.record["module_order"]],
           "evidence_path": str(evidence_path) if evidence_path else None, "export": True, "save_blend": True,
           "renders": canonical_render_specs() if canonical_renders else [], "samples": 32}
    return run_harness(job, cand.root / "build", time_limit_s=time_limit_s)


def export_candidate(cand: Candidate, *, extras: dict | None = None) -> dict:
    b = cand.build
    out = {"error": None}
    try:
        if not b or not b.get("ok") or not b.get("parts_npz"):
            raise ValueError("candidate did not build")
        rec = mexport.export_glb(Path(b["parts_npz"]), Path(b["materials_json"]), cand.root / "model.glb",
                                 extras={"modeler": {"candidate": cand.id, "program_set_sha256": cand.record.get("program_set_sha256"),
                                                     **(extras or {})}})
        out.update(rec)
    except Exception as e:  # noqa: BLE001 - reported to the author
        out["error"] = f"{type(e).__name__}: {e}"
        out["contract"] = {"ok": False, "failures": ["export_exception"]}
    (cand.root / "export.json").write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    return out
