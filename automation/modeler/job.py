"""The generic job: photos -> evidence -> author turns (program, Blender, export, observe, AR) -> evaluation -> manifest.

    python -m modeler.job --request request.json --output data/modeler/jobs/<name> [--author package|scripted]
                          [--script decisions.json] [--max-turns N] [--author-timeout-min M] [--no-ar] [--resume]

Nothing product-specific lives here. The author driver is the only model in the loop; the host never edits a
program. Every turn, candidate, observation and decision is journaled under the job folder.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys
import time
import traceback

import numpy as np

from . import author as mauthor
from . import evaluate as mevaluate
from .candidates import Candidate, CandidateStore, build_candidate, export_candidate
from .intake import run_intake
from .observe import observe_candidate
from .paths import JOBS, blender_executable
from .request import Request
from .worker import run_harness

PROTOCOL_VERSION = "modeler_protocol_v1"


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


class Journal:
    def __init__(self, path: Path):
        self.path = path

    def write(self, event: str, **data) -> None:
        row = {"time": now(), "event": event, **data}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, default=str) + "\n")


class Job:
    def __init__(self, job_dir: Path, request: Request, *, driver, ar: bool = True, log=print, evaluator_driver=None,
                 identity_checklist: list[str] | None = None, seed_program: Path | None = None):
        self.dir = Path(job_dir)
        self.seed_program = Path(seed_program) if seed_program else None
        self.seed_record = None
        self.request = request
        self.driver = driver
        self.evaluator_driver = evaluator_driver
        self.ar = ar
        self.log = log
        self.journal = Journal(self.dir / "journal.jsonl")
        self.store = CandidateStore(self.dir)
        self.identity_checklist = identity_checklist or []
        self.turn_index = 0
        self.stagnation = 0
        self.extra_images: list[dict] = []
        self.started = time.time()
        self.turn_log: list[dict] = []

    # ------------------------------------------------------------------ setup
    @classmethod
    def create(cls, request_path: Path, job_dir: Path, **kw) -> "Job":
        request = Request.load(request_path)
        job_dir = Path(job_dir)
        job_dir.mkdir(parents=True, exist_ok=True)
        (job_dir / "request.json").write_text(json.dumps({"loaded_from": str(Path(request_path).resolve()), **request.to_dict()}, indent=1),
                                              encoding="utf-8")
        job = cls(job_dir, request, **kw)
        job.journal.write("job_created", request=str(request_path), driver=job.driver.name, blender=str(blender_executable()))
        return job

    @classmethod
    def open(cls, job_dir: Path, **kw) -> "Job":
        """An existing job folder (its request copy, evidence, candidates and turns), for finalizing or inspection."""
        job_dir = Path(job_dir)
        raw = json.loads((job_dir / "request.json").read_text(encoding="utf-8"))
        raw.pop("loaded_from", None)
        request = Request.from_dict(raw)
        job = cls(job_dir, request, **kw)
        # the original run's driver and wall time come from the journal, not from this finalize invocation
        jp = job_dir / "journal.jsonl"
        if jp.exists():
            rows = [json.loads(l) for l in jp.read_text(encoding="utf-8").splitlines() if l.strip()]
            created = [r for r in rows if r.get("event") == "job_created"]
            finished = [r for r in rows if r.get("event") == "job_finished"]
            if created:
                job.recovered_driver_name = created[-1].get("driver")
                t0 = time.mktime(time.strptime(created[-1]["time"], "%Y-%m-%dT%H:%M:%S"))
                t1 = time.mktime(time.strptime(finished[0]["time"], "%Y-%m-%dT%H:%M:%S")) if finished else time.time()
                job.recovered_elapsed_min = round((t1 - t0) / 60, 1)
        turns = sorted(p for p in (job_dir / "turns").glob("turn-*") if p.is_dir()) if (job_dir / "turns").exists() else []
        job.turn_index = len(turns)
        by_turn = {}
        for c in job.store.all():
            by_turn[c.record.get("turn")] = c
        for t in turns:
            d = t / "decision.json"
            if d.exists():
                dec = json.loads(d.read_text(encoding="utf-8"))
                decision, meta = dec.get("decision", {}), dec.get("meta", {})
                idx = int(t.name.split("-")[1])
                entry = {"turn": idx, "decision": decision.get("decision"), "author_seconds": meta.get("seconds"), "recovered": True}
                if meta.get("cost_usd") is not None:
                    entry["cost_usd"] = meta["cost_usd"]
                if decision.get("decision") == "submit_program":
                    entry["changed_modules"] = sorted(decision.get("modules") or [])
                    entry["rationale"] = (decision.get("rationale") or "")[:300]
                    c = by_turn.get(idx)
                    if c is not None:
                        entry.update(candidate=c.id, valid=c.valid(), score_mm=c.score(), build_ok=(c.build or {}).get("ok"))
                elif decision.get("decision") == "request_views":
                    entry["views"] = [v["id"] for v in decision.get("views", [])]
                    entry["rationale"] = (decision.get("rationale") or "")[:300]
                else:
                    entry["deliver"] = decision.get("deliver")
                job.turn_log.append(entry)
        return job

    def finalize_only(self, finish: dict | None = None) -> dict:
        """Evaluate the incumbent and write the manifest without another author turn (a slow or interrupted run)."""
        evidence = self.evidence()
        protocol = json.loads((self.dir / "evaluation_protocol.json").read_text(encoding="utf-8"))
        if finish is None:
            finish = self.recorded_finish()
        self.journal.write("finalize_only", turns_recovered=self.turn_index, finish_recovered=bool(finish))
        return self.finalize(evidence, protocol, finish)

    def recorded_seed(self) -> dict | None:
        """The seed record of a job opened again (finalize), from the journal."""
        try:
            rows = [json.loads(l) for l in (self.dir / "journal.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
        except OSError:
            return None
        seeded = [r for r in rows if r.get("event") == "seeded"]
        if not seeded:
            return None
        r = seeded[-1]
        return {"source": r.get("source"), "modules": r.get("modules"), "program_sha256": r.get("program_sha256"), "candidate": "c0000"}

    def recorded_finish(self) -> dict | None:
        """The author's own finish decision as recorded in the last turn folder, so a re-finalize delivers the
        author's choice (until 2026-09-26 a re-finalize silently fell back to the incumbent)."""
        for turn_dir in sorted(self.dir.glob("turns/turn-*"), reverse=True):
            p = turn_dir / "response.json"
            if not p.exists():
                continue
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if isinstance(d, dict) and d.get("decision") == "finish":
                try:
                    return mauthor.validate_decision(d)
                except Exception:  # noqa: BLE001 - an unparsable finish is no finish
                    return None
        return None

    @property
    def evidence_dir(self) -> Path:
        return self.dir / "evidence"

    def evidence(self) -> dict:
        return json.loads((self.evidence_dir / "evidence.json").read_text(encoding="utf-8"))

    def ensure_evidence(self) -> dict:
        if (self.evidence_dir / "evidence.json").exists():
            return self.evidence()
        width, prov = self.request.front_width_mm()
        self.log(f"[intake] {len(self.request.photos)} photos, front width {width} mm ({prov['source']})")
        ev = run_intake(self.request, self.dir, front_width_mm=width, width_provenance=prov)
        self.journal.write("intake_done", seconds=ev["seconds"], views=list(ev["views"]), held_out=list(ev["held_out"]),
                           flags={k: v["flags"] for k, v in ev["views"].items()})
        return ev

    def write_protocol(self, evidence: dict) -> dict:
        """Frozen evaluation protocol (scored views, metrics, incumbent rule) recorded before the first turn."""
        p = self.dir / "evaluation_protocol.json"
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
        from .candidates import SCORE_WEIGHTS
        from .observe import AR_VIEWS, CANONICAL_VIEWS
        try:
            from .calibration import protocol_calibration
            calibration = protocol_calibration(getattr(self.evaluator_driver, "name", None) if self.evaluator_driver is not None else None)
        except Exception as e:  # noqa: BLE001 - a job must freeze even if the calibration set is unreadable; then the bar is uncalibrated
            calibration = {"calibrated": False, "reasons": [f"calibration unreadable: {type(e).__name__}: {e}"]}
        protocol = {
            "version": PROTOCOL_VERSION, "frozen_at": now(),
            "fit_views": [k for k, v in evidence["views"].items() if v["view"] in ("front", "back", "left", "right")],
            "held_out_views": list(evidence["held_out"]),
            "camera_fit": "bsa.cameras.ViewFit multi-start silhouette fit per photo per candidate (same seeds and levels for every candidate; warm start from the previous candidate's camera added)",
            "metrics": ["per fit view: silhouette IoU, symmetric contour mean/p95 (px, mm at the fitted scale, % of width)",
                        "front view: rendered lens parts vs measured lens outlines, contour mean/p95 mm",
                        "held-out view: the same, reported only in the evaluation"],
            "incumbent_rule": {"valid": "contract ok AND AR runtime_compatible AND >= 1 optical mesh",
                               "score": "weighted mean of contour_mean_mm over fit views + lens outline mm; lower is better", "weights": SCORE_WEIGHTS},
            "canonical_views": [v["id"] for v in CANONICAL_VIEWS], "ar_views": list(AR_VIEWS),
            "identity_checklist": self.identity_checklist,
            "evaluator_protocol": mevaluate.PROTOCOL,
            "visual_bar_calibrated": bool(calibration.get("calibrated")),
            "calibration": calibration,
            "gate_thresholds_mm": mevaluate.GATE_THRESHOLDS_MM, "report_only_thresholds_mm": mevaluate.REPORT_ONLY_MM,
            "provisional_thresholds_mm": mevaluate.PROVISIONAL_THRESHOLDS_MM, "threshold_provenance": mevaluate.THRESHOLD_PROVENANCE,
            "scale": evidence["scale"],
            "leakage": {"author_inputs": "fit-view photos, their mattes and measurements; the author never receives the held-out photo",
                        "evaluator_inputs": "all photos including held out, candidate renders, metrics; no author rationale",
                        "donor_assets": "none" if not self.request.donor else self.request.donor,
                        "previously_solved_assets": "none used; every candidate is built from the author's program alone"},
        }
        p.write_text(json.dumps(protocol, indent=1), encoding="utf-8")
        self.journal.write("protocol_frozen", path=str(p))
        return protocol

    # ------------------------------------------------------------------ author context
    def base_images(self, evidence: dict) -> list[dict]:
        images = []
        for vid, v in evidence["views"].items():
            ap = v.get("author_photo")
            if ap:
                images.append({"id": f"photo_{vid}", "label": f"product photo, view {v['view']} (cropped, {ap['size'][0]}x{ap['size'][1]} px)",
                               "path": ap["path"], "sha256": mauthor.sha256_file(ap["path"])})
            if v.get("measured_overlay"):
                images.append({"id": "front_measured", "label": "front photo with the measured lens outlines (green), symmetry axis (red) and y = 0 line (blue)",
                               "path": v["measured_overlay"], "sha256": mauthor.sha256_file(v["measured_overlay"])})
        return images

    def candidate_images(self, cand: Candidate, prefix: str) -> list[dict]:
        obs = cand.observation or {}
        out = []
        labels = {"photo_match": "photo-matched renders: [photo | render from the fitted camera | overlay red photo edge / green render edge] per fit view (EEVEE preview: judge SHAPE here; colours and lens transparency are approximate)",
                  "clay": "neutral clay renders (front, right side, top, back, three-quarter): shape and profile only",
                  "textured": "textured EEVEE preview renders (front, three-quarter): materials and lens see-through are approximate here; judge them in the AR sheet",
                  "ar": "the exported GLB in the ACTUAL AR renderer (front, yaw 35, roll 25, asset back): the authoritative view of materials, lens optics and runtime behaviour"}
        for key, p in (obs.get("sheets") or {}).items():
            if p and Path(p).is_file():
                out.append({"id": f"{prefix}_{key}", "label": f"{cand.id}: {labels.get(key, key)}", "path": p, "sha256": mauthor.sha256_file(p)})
        return out

    def run_state(self, evidence: dict, last: Candidate | None, incumbent: Candidate | None, max_turns: int) -> dict:
        cands = [c.summary() for c in self.store.all()]
        state = {"turn": self.turn_index, "turns_left": max_turns - self.turn_index, "max_turns": max_turns,
                 "elapsed_min": round((time.time() - self.started) / 60, 1),
                 "incumbent": incumbent.summary() if incumbent else None,
                 "last_candidate": last.summary() if last else None, "candidates": cands,
                 "stagnation_turns": self.stagnation, "recent_turns": self.turn_log[-6:]}
        if last is not None:
            b = last.build or {}
            failed = [m for m in b.get("module_results", []) if not m.get("ok")]
            if failed:
                state["last_build_failure"] = {"module": failed[0]["name"], "error": failed[0]["error"], "traceback": failed[0]["traceback"]}
            elif b.get("error"):
                state["last_build_failure"] = {"module": None, "error": b["error"], "traceback": b.get("traceback")}
            state["last_inventory"] = b.get("inventory")
            state["last_notes"] = b.get("notes")
            e = last.export or {}
            if e:
                contract = e.get("contract") or {}
                failed_checks = {k: {kk: vv for kk, vv in v.items() if kk in ("value", "limit")} for k, v in (contract.get("checks") or {}).items() if not v.get("pass")}
                topology = {k: {kk: v.get(kk) for kk in ("watertight", "boundary_edges", "nonmanifold_edges", "misoriented_edges", "degenerate_faces", "triangles")}
                            for k, v in (contract.get("parts") or {}).items()}
                state["last_export"] = {"error": e.get("error"), "contract_ok": contract.get("ok"),
                                        "contract_failures": contract.get("failures"), "failed_checks": failed_checks,
                                        "part_topology": topology, "notes": e.get("notes"), "parts": e.get("parts")}
            o = last.observation or {}
            if o:
                state["last_metrics"] = {"summary": o.get("summary"),
                                         "per_view": {k: {kk: v.get(kk) for kk in ("view", "iou", "contour_mean_mm", "contour_p95_mm", "contour_mean_pct_width", "lens_outline")}
                                                      for k, v in o.get("views", {}).items()}}
        if self.stagnation >= int(self.request.limits.get("stagnation_turns", 3)):
            state["stagnation_notice"] = ("The incumbent has not improved for several turns. Either try a materially different construction of "
                                          "the worst-measured part (not parameter nudges), or finish with the incumbent and state what would need to change.")
        base = incumbent or last
        if base is not None:
            state["base_program"] = {"candidate": base.id, "modules": base.modules()}
        return state

    # ------------------------------------------------------------------ turn execution
    def seed(self, evidence: dict) -> Candidate:
        """Start from an existing program (a previous job's candidate program folder): built, exported and observed
        in THIS job as its first candidate, so the author begins from a measured incumbent and can rewrite single
        modules. The source is recorded in the journal and the manifest; nothing else is carried over."""
        import hashlib
        from .candidates import MODULE_ORDER
        modules = {}
        for name in MODULE_ORDER:
            p = self.seed_program / f"{name}.py"
            if p.exists():
                modules[name] = p.read_text(encoding="utf-8")
        if not modules:
            raise FileNotFoundError(f"no program modules under {self.seed_program}")
        turn_dir = self.dir / "turns" / "seed"
        turn_dir.mkdir(parents=True, exist_ok=True)
        decision = mauthor.validate_decision({"decision": "submit_program", "modules": modules, "base": None,
                                              "rationale": f"seed: program copied from {self.seed_program} (no author turn)"})
        (turn_dir / "decision.json").write_text(json.dumps({"decision": decision, "meta": {"driver": "seed"}}, indent=1), encoding="utf-8")
        digest = hashlib.sha256("".join(modules[k] for k in sorted(modules)).encode("utf-8")).hexdigest()
        self.journal.write("seeded", source=str(self.seed_program), modules=sorted(modules), program_sha256=digest)
        self.log(f"[seed] building the seed program from {self.seed_program} ({sorted(modules)})")
        cand = self.execute_submit(decision, evidence, turn_dir)
        self.seed_record = {"source": str(self.seed_program), "candidate": cand.id, "modules": sorted(modules), "program_sha256": digest,
                            "valid": cand.valid(), "score_mm": cand.score()}
        self.turn_log.append({"turn": "seed", "decision": "seed", "candidate": cand.id, "valid": cand.valid(), "score_mm": cand.score(),
                              "author_seconds": 0, "rationale": decision["rationale"]})
        return cand

    def execute_submit(self, decision: dict, evidence: dict, turn_dir: Path) -> Candidate:
        base_ref = decision.get("base", "incumbent")
        base = None
        if base_ref == "incumbent":
            base = self.store.incumbent() or (self.store.all()[-1] if self.store.ids() else None)
        elif base_ref:
            base = self.store.get(base_ref)
        cand = self.store.create(decision["modules"], base=base, turn=self.turn_index, rationale=decision.get("rationale", ""),
                                 author_ref=str(turn_dir.name))
        self.log(f"[turn {self.turn_index}] building {cand.id} (modules {decision['modules'].keys() and list(decision['modules'])}, base {base.id if base else None})")
        t0 = time.time()
        build = build_candidate(cand, self.evidence_dir / "evidence.json", time_limit_s=int(self.request.limits.get("blender_time_limit_s", 300)))
        self.journal.write("candidate_built", candidate=cand.id, ok=build.get("ok"), seconds=round(time.time() - t0, 1))
        if not build.get("ok"):
            self.log(f"[turn {self.turn_index}] {cand.id} build failed: {[m.get('error') for m in build.get('module_results', []) if not m.get('ok')] or build.get('error')}")
            return cand
        exp = export_candidate(cand, extras={"job": self.dir.name, "product": self.request.product_id})
        self.journal.write("candidate_exported", candidate=cand.id, contract_ok=(exp.get("contract") or {}).get("ok"), error=exp.get("error"))
        if exp.get("error") or not (exp.get("contract") or {}).get("ok"):
            self.log(f"[turn {self.turn_index}] {cand.id} export/contract failed: {exp.get('error') or exp.get('contract', {}).get('failures')}")
            # observe anyway (renders and metrics help the author), but without the AR harness
        prev = None
        inc = self.store.incumbent()
        if inc is not None and inc.observation:
            prev = inc.observation.get("views")
        t0 = time.time()
        try:
            observe_candidate(cand.root, build, evidence, self.evidence_dir, held_out_ids=set(evidence["held_out"]), previous_cameras=prev,
                              ar=self.ar and cand.glb is not None and (exp.get("contract") or {}).get("ok", False), glb_path=cand.glb,
                              time_limit_s=int(self.request.limits.get("blender_time_limit_s", 300)))
        except Exception as e:  # noqa: BLE001 - an observation failure is recorded, the loop continues
            self.log(f"[turn {self.turn_index}] observation failed: {e}")
            (cand.root / "observe").mkdir(exist_ok=True)
            (cand.root / "observe" / "observation_error.json").write_text(json.dumps({"error": f"{type(e).__name__}: {e}", "traceback": traceback.format_exc()}), encoding="utf-8")
        self.journal.write("candidate_observed", candidate=cand.id, seconds=round(time.time() - t0, 1), score=cand.score(), valid=cand.valid(),
                           summary=(cand.observation or {}).get("summary"))
        self.log(f"[turn {self.turn_index}] {cand.id}: valid={cand.valid()} score={cand.score()} {json.dumps((cand.observation or {}).get('summary'))[:400]}")
        return cand

    def execute_request_views(self, decision: dict, turn_dir: Path) -> list[dict]:
        ref = decision.get("candidate", "incumbent")
        cand = self.store.incumbent() if ref == "incumbent" else self.store.get(ref)
        if cand is None:
            cand = self.store.all()[-1] if self.store.ids() else None
        if cand is None or not cand.build or not cand.build.get("blend"):
            return []
        specs = []
        for v in decision["views"]:
            specs.append({"id": v["id"], "kind": v["kind"], "width": 640, "height": 480, "transparent": True,
                          "camera": {"type": "orbit", "yaw": v["yaw"], "pitch": v["pitch"], "roll": v["roll"], "ortho": v["ortho"],
                                     "px_per_mm": v["px_per_mm"], "target": v["target"], "distance_mm": 600}})
        out_dir = turn_dir / "extra_views"
        r = run_harness({"mode": "render_only", "blend_path": cand.build["blend"], "renders": specs, "samples": 32}, out_dir,
                        time_limit_s=int(self.request.limits.get("blender_time_limit_s", 300)))
        images = []
        for row in r.get("renders", []):
            if row.get("path"):
                images.append({"id": f"extra_{row['id']}", "label": f"{cand.id}: requested view {row['id']} ({row['kind']}, yaw {row['camera']['yaw']}, pitch {row['camera']['pitch']})",
                               "path": row["path"], "sha256": mauthor.sha256_file(row["path"])})
        self.journal.write("extra_views", candidate=cand.id, count=len(images), ok=r.get("ok"))
        return images

    # ------------------------------------------------------------------ the loop
    def run(self) -> dict:
        evidence = self.ensure_evidence()
        protocol = self.write_protocol(evidence)
        max_turns = int(self.request.limits.get("max_turns", 10))
        wall_limit = float(self.request.limits.get("wall_time_limit_min", 180)) * 60
        job_meta = {"job": self.dir.name, "product_id": self.request.product_id, "limits": self.request.limits, "protocol": PROTOCOL_VERSION}
        finish = None
        last = None
        best_score_seen = None
        if self.seed_program is not None and not self.store.ids():
            last = self.seed(evidence)
        while self.turn_index < max_turns and (time.time() - self.started) < wall_limit:
            incumbent = self.store.incumbent()
            turn_dir = self.dir / "turns" / f"turn-{self.turn_index:04d}"
            turn_dir.mkdir(parents=True, exist_ok=True)
            images = self.base_images(evidence)
            if last is not None:
                images += self.candidate_images(last, "last")
            if incumbent is not None and (last is None or incumbent.id != last.id):
                images += self.candidate_images(incumbent, "incumbent")
            images += self.extra_images
            images = images[:mauthor.MAX_IMAGES]
            self.extra_images = []
            state = self.run_state(evidence, last, incumbent, max_turns)
            request = mauthor.build_request(job_meta=job_meta, evidence=evidence, state=state, images=images)
            t0 = time.time()
            try:
                decision, meta = self.driver.decide(turn_dir, request, images, log=self.log)
            except Exception as e:  # noqa: BLE001
                self.journal.write("author_failed", turn=self.turn_index, error=f"{type(e).__name__}: {e}")
                self.log(f"[turn {self.turn_index}] author failed: {e}")
                break
            (turn_dir / "decision.json").write_text(json.dumps({"decision": decision, "meta": meta}, indent=1), encoding="utf-8")
            self.journal.write("decision", turn=self.turn_index, kind=decision["decision"], meta=meta)
            entry = {"turn": self.turn_index, "decision": decision["decision"], "author_seconds": round(time.time() - t0, 1)}
            if decision["decision"] == "finish":
                finish = decision
                entry["deliver"] = decision["deliver"]
                self.turn_log.append(entry)
                break
            if decision["decision"] == "request_views":
                self.extra_images = self.execute_request_views(decision, turn_dir)
                entry["views"] = [v["id"] for v in decision["views"]]
                self.turn_log.append(entry)
                self.turn_index += 1
                continue
            cand = self.execute_submit(decision, evidence, turn_dir)
            last = cand
            entry.update(candidate=cand.id, changed_modules=decision["modules"] and sorted(decision["modules"]), valid=cand.valid(), score_mm=cand.score(),
                         build_ok=(cand.build or {}).get("ok"), rationale=decision.get("rationale", "")[:300])
            self.turn_log.append(entry)
            if decision.get("deliver_if_valid") and cand.valid():
                # the submission is the author's finish too: a one-turn repair no longer needs a second paid call
                finish = {"decision": "finish", "deliver": cand.id, "status_claim": "best_effort",
                          "note": "delivered on submit (deliver_if_valid): " + (decision.get("rationale") or "")[:300]}
                self.journal.write("delivered_on_submit", turn=self.turn_index, candidate=cand.id)
                self.log(f"[turn {self.turn_index}] {cand.id} delivered on submit")
                self.turn_index += 1
                break
            s = cand.score()
            if s is not None and (best_score_seen is None or s < best_score_seen * 0.97):
                best_score_seen = s
                self.stagnation = 0
            else:
                self.stagnation += 1
            if self.stagnation >= 2 * int(self.request.limits.get("stagnation_turns", 3)):
                self.journal.write("stagnation_stop", turn=self.turn_index, stagnation=self.stagnation)
                self.log(f"[turn {self.turn_index}] stopping: no improvement for {self.stagnation} turns")
                self.turn_index += 1
                break
            self.turn_index += 1
        return self.finalize(evidence, protocol, finish)

    # ------------------------------------------------------------------ delivery
    def choose_delivery(self, finish: dict | None) -> tuple[Candidate | None, str]:
        inc = self.store.incumbent()
        if finish and finish.get("deliver") not in (None, "incumbent"):
            try:
                chosen = self.store.get(finish["deliver"])
            except KeyError:
                chosen = None
            if chosen is not None and chosen.valid():
                if inc is None or chosen.score() <= inc.score() * 1.25:
                    return chosen, "author's choice (valid, within 25 % of the incumbent's score)"
                return inc, f"incumbent kept: the author's choice {chosen.id} scores {chosen.score()} vs {inc.score()}"
            if chosen is not None:
                return inc, f"incumbent kept: the author's choice {chosen.id} is not valid"
        return inc, "incumbent (strongest observed valid candidate)"

    def finalize(self, evidence: dict, protocol: dict, finish: dict | None) -> dict:
        delivered, rule = self.choose_delivery(finish)
        evaluation = None
        eval_meta = None
        eval_dir = self.dir / "evaluation"
        stored = eval_dir / "evaluation.json"
        # An evaluation of the delivered candidate produced by `modeler.evaluate_asset` (an external agent answering
        # the current protocol's package) is the job's evaluation when it names the same asset; otherwise a stored evaluation of the same
        # candidate is reused.
        v2 = delivered.root / "evaluation_v2" / "evaluation.json" if delivered is not None else None
        if evaluation is None and v2 is not None and v2.exists():
            rec = json.loads(v2.read_text(encoding="utf-8"))
            asset_sha = (delivered.export or {}).get("sha256")
            if rec.get("evaluation") and (not rec.get("asset_sha256") or rec.get("asset_sha256") == asset_sha):
                evaluation = mevaluate.validate_evaluation(rec["evaluation"])
                eval_meta = dict(rec.get("meta") or {}, source=str(v2))
                eval_dir.mkdir(exist_ok=True)
                (eval_dir / "evaluation.json").write_text(json.dumps({"evaluation": evaluation, "meta": eval_meta, "candidate": delivered.id}, indent=1), encoding="utf-8")
                self.journal.write("evaluation_adopted", candidate=delivered.id, source=str(v2))
        if evaluation is None and delivered is not None and stored.exists():
            prev = json.loads(stored.read_text(encoding="utf-8"))
            if prev.get("candidate") in (None, delivered.id):
                evaluation, eval_meta = prev.get("evaluation"), dict(prev.get("meta") or {}, reused=True)
                self.journal.write("evaluation_reused", candidate=delivered.id)
        if delivered is not None and self.evaluator_driver is not None and evaluation is None:
            eval_dir.mkdir(exist_ok=True)
            try:
                request, images = mevaluate.write_evaluator_package(self.dir, delivered.root, evidence, self.identity_checklist, eval_dir,
                                                                    glb_path=delivered.glb)
                evaluation, eval_meta = self.evaluator_driver.decide(eval_dir, request, images, role="evaluator",
                                                                     schema_check=mevaluate.validate_evaluation, log=self.log)
                (eval_dir / "evaluation.json").write_text(json.dumps({"evaluation": evaluation, "meta": eval_meta, "candidate": delivered.id}, indent=1), encoding="utf-8")
            except Exception as e:  # noqa: BLE001
                self.journal.write("evaluation_failed", error=f"{type(e).__name__}: {e}")
                self.log(f"[final] evaluation failed: {e}")
        input_flags = sorted({f for v in evidence["views"].values() for f in v["flags"]})
        status = mevaluate.decide_status(candidate_valid=delivered is not None and delivered.valid(),
                                         metrics=(delivered.observation or {}).get("summary") if delivered else None,
                                         heldout=delivered.heldout if delivered else None, evaluation=evaluation,
                                         input_flags=input_flags, protocol_calibrated=bool(protocol.get("visual_bar_calibrated")))
        manifest = self.write_manifest(evidence, protocol, delivered, rule, finish, status, evaluation)
        self.journal.write("job_finished", status=status["status"], delivered=delivered.id if delivered else None)
        self.log(f"[final] status {status['status']}; delivered {delivered.id if delivered else None} ({rule})")
        return manifest

    def write_manifest(self, evidence: dict, protocol: dict, delivered: Candidate | None, rule: str, finish: dict | None,
                       status: dict, evaluation: dict | None) -> dict:
        glb = delivered.glb if delivered else None
        exp = delivered.export if delivered else None
        obs = delivered.observation if delivered else None
        width_mm = None
        if obs and obs.get("bbox_mm"):
            width_mm = round(float(obs["bbox_mm"][1][0] - obs["bbox_mm"][0][0]), 2)
        deliver_dir = self.dir / "deliverable"
        deliver_dir.mkdir(exist_ok=True)
        delivered_path = None
        if glb is not None:
            delivered_path = deliver_dir / f"{self.request.product_id}.glb"
            shutil.copy2(glb, delivered_path)
        manifest = {
            "schema_version": 1, "job": self.dir.name, "product_id": self.request.product_id, "written": now(),
            "status": status["status"], "status_detail": status, "visual_bar_calibrated": bool(protocol.get("visual_bar_calibrated")),
            "delivered_candidate": delivered.id if delivered else None, "delivery_rule": rule,
            "author_finish": finish,
            "asset": None if glb is None else {"path": str(delivered_path), "source_candidate_glb": str(glb), "sha256": exp.get("sha256"),
                                                "bytes": exp.get("bytes"), "triangles": (exp.get("receipt") or {}).get("triangles"),
                                                "contract": {"ok": (exp.get("contract") or {}).get("ok"), "failures": (exp.get("contract") or {}).get("failures")}},
            "mounting": {"units": "metres", "up": "+Y", "front": "+Z", "origin": "bridge underside on the symmetry axis (bsa.export rule)",
                         "origin_receipt": (exp or {}).get("receipt", {}).get("origin"),
                         "front_width_mm_measured": width_mm, "temple_clip_z_m_recommended": -0.14,
                         "lens_env_intensity_recommended": ((obs or {}).get("summary") or {}).get("lens_env_intensity_recommended"),
                         "handover": "?model=<url>&width=<front_width_mm_measured>&clip=-0.14&sha256=<sha256>" + ("&lensenv=<lens_env_intensity_recommended>" if ((obs or {}).get("summary") or {}).get("lens_env_intensity_recommended") is not None else "")},
            "scale": {**evidence["scale"], "note": "millimetres are nominal unless a physical dimension was supplied"},
            "evidence": {"inputs": evidence["inputs"], "fit_views": protocol["fit_views"], "held_out_views": protocol["held_out_views"],
                         "evidence_json": str(self.evidence_dir / "evidence.json")},
            "measurements": {"author_visible": (obs or {}).get("summary"), "held_out": (delivered.heldout or {}).get("summary") if delivered else None},
            "evaluation": evaluation,
            "candidates": [c.summary() for c in self.store.all()],
            "program": {"candidate": delivered.id, "modules": delivered.record.get("modules"), "program_dir": str(delivered.program_dir),
                        "blend": (delivered.build or {}).get("blend")} if delivered else None,
            "provenance": protocol["leakage"] | {"author_driver": getattr(self, "recovered_driver_name", None) or self.driver.name, "turns": self.turn_log,
                                                 "seed_program": self.seed_record or self.recorded_seed(),
                                                 "turns_dir": str(self.dir / "turns"), "journal": str(self.journal.path)},
            "runtime": {"elapsed_min": getattr(self, "recovered_elapsed_min", None) or round((time.time() - self.started) / 60, 1),
                        "turns": self.turn_index},
        }
        # A re-finalize must not erase the owner's recorded verdict on this same asset (modeler.owner_verdict).
        mp = self.dir / "manifest.json"
        if mp.exists():
            try:
                old = json.loads(mp.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                old = {}
            ov = old.get("owner_verdict")
            if ov and manifest.get("asset") and ov.get("asset_sha256") == manifest["asset"].get("sha256"):
                manifest["owner_verdict"] = ov
                if ov.get("verdict") == "accept":
                    sd = dict(manifest["status_detail"], previous_status=manifest["status"], previous_reasons=list(manifest["status_detail"].get("reasons") or []),
                              accepted_by="owner", reasons=[f"owner accepted the delivered asset ({ov['asset_sha256'][:12]}…) in the {ov.get('medium')} on {str(ov.get('when'))[:10]}"
                                                            + (f": {ov['note']}" if ov.get("note") else "")], status="accepted")
                    manifest["status"], manifest["status_detail"] = "accepted", sd
                elif ov.get("verdict") in ("reject", "borderline"):
                    sd = dict(manifest["status_detail"])
                    sd["owner_rejected" if ov["verdict"] == "reject" else "owner_borderline"] = True
                    manifest["status_detail"] = sd
                self.journal.write("owner_verdict_kept", verdict=ov.get("verdict"))
        mp.write_text(json.dumps(manifest, indent=1, default=str), encoding="utf-8")
        return manifest


# --------------------------------------------------------------------------- CLI
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--request", type=Path, help="request JSON (not needed with --finalize)")
    ap.add_argument("--output", type=Path, required=True, help="job folder (created)")
    ap.add_argument("--author", choices=("package", "scripted", "astra"), default=None, help="overrides request.author.driver")
    ap.add_argument("--script", type=Path, help="scripted driver: JSON list of decisions")
    ap.add_argument("--astra-cap", type=int, help="astra: maximum paid calls for this authorization (1..10); required")
    ap.add_argument("--astra-ledger", type=Path, help="astra: shared ledger file outside the job folder; required")
    ap.add_argument("--astra-env", type=Path, help="astra: dotenv file holding the credential (never printed)")
    ap.add_argument("--astra-api-key-env", default="OPENAI_API_KEY")
    ap.add_argument("--astra-effort", default="high", choices=("low", "medium", "high", "xhigh", "max"))
    ap.add_argument("--astra-cap-usd", type=float, help="astra: cumulative dollar cap shared by every Astra call of this ledger; required")
    ap.add_argument("--astra-reserve-evaluator-usd", type=float, default=2.0, help="astra: dollars the author leaves for the evaluator's call")
    ap.add_argument("--astra-usd-ledger", type=Path, help="astra: dollar ledger file shared across call ledgers (default <ledger>.usd.json)")
    ap.add_argument("--evaluator", choices=("package", "scripted", "astra", "none"), default="package")
    ap.add_argument("--evaluator-script", type=Path)
    ap.add_argument("--max-turns", type=int)
    ap.add_argument("--author-timeout-min", type=float, default=60.0)
    ap.add_argument("--no-ar", action="store_true", help="skip the AR harness (development only; candidates cannot become valid)")
    ap.add_argument("--checklist", type=Path, help="JSON list of identity features for the evaluator")
    ap.add_argument("--seed-program", type=Path, help="start from this program folder (a previous candidate's program/): built and observed as this job's first candidate, recorded in the manifest")
    ap.add_argument("--finalize", action="store_true", help="open the existing job in --output, evaluate the incumbent, write the manifest; no author turn")
    args = ap.parse_args(argv)
    if args.finalize:
        request = Request.from_dict({k: v for k, v in json.loads((args.output / "request.json").read_text(encoding="utf-8")).items() if k != "loaded_from"})
    else:
        request = Request.load(args.request)
    # --finalize takes no author turn: no author driver (and no credential) is needed unless one is named explicitly.
    driver_name = args.author or ("scripted" if args.finalize else request.author.get("driver", "package"))
    if driver_name == "scripted":
        if not args.script and not args.finalize:
            ap.error("--script is required with the scripted author")
        driver = mauthor.ScriptedDriver(json.loads(args.script.read_text(encoding="utf-8")) if args.script else [])
    secret_holder = {}

    def astra_secret():
        import os
        if secret_holder.get("v"):
            return secret_holder["v"]
        if not args.astra_cap or not args.astra_ledger or not args.astra_cap_usd:
            ap.error("astra needs an explicit --astra-cap (1..10 calls), --astra-cap-usd and --astra-ledger; no paid call without a cap")
        if args.astra_ledger.resolve().is_relative_to(args.output.resolve()):
            ap.error("--astra-ledger must live outside the job folder")
        secret = None
        if args.astra_env:
            from dotenv import dotenv_values
            secret = dotenv_values(args.astra_env).get(args.astra_api_key_env)
        if not secret:
            secret = os.environ.get(args.astra_api_key_env)
        if not secret:
            ap.error(f"no credential in the explicit source ({args.astra_api_key_env})")
        secret_holder["v"] = secret
        return secret

    if driver_name == "astra":
        from . import author_astra
        driver = author_astra.AstraAuthorDriver(astra_secret(), budget_path=args.astra_ledger, maximum_calls=args.astra_cap,
                                                cap_usd=args.astra_cap_usd, reasoning_effort=args.astra_effort,
                                                reserve_usd=args.astra_reserve_evaluator_usd if args.evaluator == "astra" else 0.0,
                                                usd_ledger_path=args.astra_usd_ledger)
    elif driver_name != "scripted":   # (until 2026-09-26 this branch also replaced the scripted driver with the package driver)
        driver = mauthor.PackageDriver(timeout_s=int(args.author_timeout_min * 60))
    if args.evaluator == "none":
        evaluator = None
    elif args.evaluator == "astra":
        from . import author_astra
        evaluator = author_astra.AstraEvaluatorDriver(astra_secret(), budget_path=args.astra_ledger, maximum_calls=args.astra_cap,
                                                      cap_usd=args.astra_cap_usd, reasoning_effort=args.astra_effort,
                                                      usd_ledger_path=args.astra_usd_ledger)
    elif args.evaluator == "scripted":
        evaluator = mauthor.ScriptedDriver(json.loads(args.evaluator_script.read_text(encoding="utf-8")) if args.evaluator_script else [])
    else:
        evaluator = mauthor.PackageDriver(timeout_s=int(args.author_timeout_min * 60))
    secret_holder.clear()
    checklist = json.loads(args.checklist.read_text(encoding="utf-8")) if args.checklist else []
    if isinstance(checklist, dict):
        checklist = list(checklist.get("identity_features") or []) + [f"condition: {c}" for c in checklist.get("conditions_for_success") or []]
    if args.max_turns:
        request.limits["max_turns"] = args.max_turns
    if args.finalize:
        job = Job.open(args.output, driver=driver, evaluator_driver=evaluator, ar=not args.no_ar, identity_checklist=checklist)
        manifest = job.finalize_only()
    else:
        job = Job.create(args.request, args.output, driver=driver, evaluator_driver=evaluator, ar=not args.no_ar, identity_checklist=checklist,
                         seed_program=args.seed_program)
        job.request = request
        manifest = job.run()
    print(json.dumps({"status": manifest["status"], "delivered": manifest["delivered_candidate"], "asset": manifest["asset"]}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
