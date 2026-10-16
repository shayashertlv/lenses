"""The facet lab as a bounded stage of a generic job, with its own scene and image history."""
from __future__ import annotations

import asyncio
import concurrent.futures
import copy
import hashlib
import io
from pathlib import Path
import threading

from agents.tool import ToolOutputImage, ToolOutputText
from PIL import Image

from ..loop import image_key
from .lab import Lab, data_url, sha
from . import state as S
from . import tools as T


def settings(parent=None):
    from ..loop import RunSettings
    return RunSettings(effort=getattr(parent, "effort", "max"),
                       max_output_tokens=getattr(parent, "max_output_tokens", 16384), max_turns=None,
                       settled_stop_usd=getattr(parent, "settled_stop_usd", "20"), tool_choice="required",
                       reset_tool_choice=False, finish_tool="facet_finish", finish_k=4)


def registry(lab, child_settings):
    """Bind the nested preview contract into both approval and execution, without changing standalone tools."""
    from ..tools.images import MAX_IMAGE_SIDE
    tools = T.registry(lab, child_settings)
    note = (f"Nested-stage images: the descriptions below refer to saved native panels. The returned image is one "
            f"compact preview per call, at most {MAX_IMAGE_SIDE} px on its long side. image_receipt preserves native "
            "panel paths, sizes and labels. For a single panel, preview_scale_xy and preview_to_panel map preview "
            "pixels to that panel; apply any reported photo transform/k afterward. Grid coordinates are not native "
            "photo pixels. Use photo_image with a native-photo crop for detail.\n\n")
    for tool in tools:
        if tool.name == "facet_crease_compare":
            tool.description = ("Native angle comparisons: each photo-first crop sheet is returned separately, "
                                "without compact-preview scaling; image_receipt records pixel sizes and hashes.\n\n"
                                + tool.description)
        elif tool.name != "facet_finish":
            tool.description = note + tool.description
    return tools


class PhotoCatalog:
    """Only the photo facts needed to build the exact stage schemas before any worker starts."""

    def __init__(self, job):
        from .package import photo_catalog
        self.rows = photo_catalog(job)
        self.lock = threading.Lock()

    def photo_ids(self):
        return [row["id"] for row in self.rows if row["role"] == "target"]

    def compare_ids(self):
        return self.photo_ids() + [row["id"] for row in self.rows if row["role"] == "colourway"]

    def image_ids(self):
        return self.compare_ids() + [row["id"] for row in self.rows if row["role"] == "reference"]

    def variant_photos_max(self):
        from .lab import VARIANT_PHOTOS_MAX, VARIANT_TARGETS_MAX
        return min(VARIANT_PHOTOS_MAX, VARIANT_TARGETS_MAX + sum(r["role"] == "colourway" for r in self.rows))


def contract(job, parent_settings, maximum_usd, owner_edit=None):
    if owner_edit:
        from . import owner_stage
        return owner_stage.contract(job, parent_settings, maximum_usd, owner_edit)
    from ..loop import StageContract, StageSpec
    from .cli import K_TOKEN, tool_contract
    child_settings = settings(parent_settings)
    text = (Path(__file__).parent / "instructions.txt").read_text(encoding="utf-8")
    if text.count(K_TOKEN) != 1:
        raise ValueError("specialist instructions must contain their finish count exactly once")
    spec = StageSpec("temple_stage", text.replace(K_TOKEN, str(child_settings.finish_k)),
                     registry(PhotoCatalog(job), child_settings), "Current job photos and geometry.",
                     child_settings, contract=tool_contract())
    return StageContract.from_spec(spec, maximum_usd)


class StageLab(Lab):
    """An isolated current-head package with append-only, compact tool images.

    New outputs contain one preview and the native panels' provenance. Earlier outputs and source photos remain
    untouched. Standalone rehearsals retain their existing image policy.
    """

    def tool_output(self, result, footer, call_id, tool_name):
        # Import locally: the generic tools package imports the nested-stage registry too.
        from ..tools.images import IMAGE_POLICY, compact_sheet

        panels, previews = [], []
        with self.state_lock:
            checkpoint = (self.s.head or {}).get("id")
        for value in result.get("images") or []:
            path = Path(value).resolve()
            if not path.is_relative_to(self.root) or not path.is_file():
                raise ValueError("stage image must be a file inside its package")
            digest = sha(path)
            with self.state_lock:
                meta = copy.deepcopy(self.state["images"].get(digest, {}))
            view = str(meta.get("photo") or result.get("photo") or meta.get("reading") or meta.get("ref") or tool_name)
            role = {"compare": "zoom" if meta.get("zoom") else "comparison", "variants": "variants",
                    "reading": "pick", "chart": "chart", "photo": "source"}.get(meta.get("kind"), "source")
            if role == "chart":
                view = "temple chart"
            with Image.open(path) as opened:
                size = list(opened.size)
                previews.append((opened.copy(), f"{view}; revision {meta.get('revision', 'source')}"))
            panels.append({**meta, "checkpoint": checkpoint, "revision": meta.get("revision", "source"),
                           "view": view, "context_role": role,
                           "sha256": digest, "path": path.relative_to(self.root).as_posix(), "size_px": size})
        if not panels:
            return [ToolOutputText(text=T.result_text(result, footer))]

        if tool_name == "facet_crease_compare":
            # Small crease differences must reach the specialist at the recorded crop's pixel scale. Retain each
            # native sheet; the generic text limiter preserves image bytes, order and adjacent labels.
            receipt = {"call_id": call_id, "checkpoint": checkpoint, "panels": panels,
                       "scaling": "none; each sheet retains its recorded photo pixel scale"}
            out = [ToolOutputText(text=T.result_text({**result, "image_policy": "native-crease-angles/1",
                                                      "image_receipt": receipt}, footer))]
            records = []
            for panel in panels:
                out.append(ToolOutputText(text=f"Photo-first angle comparison: {panel['view']}; "
                    f"{panel['path']}; {panel['size_px']}; sha256 {panel['sha256']}; no preview scaling."))
                records.append({**panel, "call_id": call_id, "output_index": len(out),
                                "context_role": "crease_angles"})
                out.append(ToolOutputImage(image_url=data_url(self.root / panel["path"]), detail="high"))
            with self.state_lock:
                self.state.setdefault("stage_images", []).extend(records)
            self.save()
            return out

        sheet = compact_sheet(previews)
        buffer = io.BytesIO()
        sheet.save(buffer, format="PNG")
        payload = buffer.getvalue()
        digest = hashlib.sha256(payload).hexdigest()
        # Reuse an unchanged single PNG's path; never overwrite a native image or an earlier compact output.
        if len(panels) == 1 and digest == panels[0]["sha256"]:
            path = self.root / panels[0]["path"]
        else:
            path = (self.root / "work" / "tool_images" / f"{digest}.png").resolve()
            if not path.is_relative_to(self.root):
                raise ValueError("stage preview must remain inside its package")
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                if sha(path) != digest:
                    raise ValueError("stage preview bytes changed")
            else:
                path.write_bytes(payload)
        record = {**(panels[0] if len(panels) == 1 else {"context_role": "comparison"}),
                  "call_id": call_id, "output_index": 2, "checkpoint": checkpoint,
                  "revision": panels[0]["revision"] if all(p["revision"] == panels[0]["revision"] for p in panels) else "mixed",
                  "view": ", ".join(panel["view"] for panel in panels),
                  "views": [panel["view"] for panel in panels], "panels": panels,
                  "sha256": digest, "path": path.relative_to(self.root).as_posix(), "size_px": list(sheet.size)}
        if len(panels) == 1:
            record["preview_scale_xy"] = [sheet.width / panels[0]["size_px"][0],
                                          sheet.height / panels[0]["size_px"][1]]
            record["preview_to_panel"] = ("u_panel = (u_preview + 0.5) / preview_scale_xy[0] - 0.5; "
                                          "v_panel = (v_preview + 0.5) / preview_scale_xy[1] - 0.5")
        body = {**result, "image_policy": IMAGE_POLICY, "image_receipt": record}
        out = [ToolOutputText(text=T.result_text(body, footer)),
               ToolOutputText(text=f"Image: call {call_id}; checkpoint {checkpoint}; "
                                   f"revision {record.get('revision', 'mixed')}; view {record['view']}; "
                                   f"{record['path']}; sha256 {digest}. "
                                   "Compact preview; native panel paths, pixel sizes and labels are in image_receipt. "
                                   "Reported transform/k applies to saved panels: use preview_to_panel first for a "
                                   "single panel. Grid pixels are not native photo pixels; photo_image provides crop detail."),
               ToolOutputImage(image_url=data_url(path), detail="high")]
        # Always include the preview, including on retries: there is no unseen-output deduplication state.
        with self.state_lock:
            self.state.setdefault("stage_images", []).append(record)
        self.save()
        return out

    def context_image_stubs(self, images):
        with self.state_lock:
            records = [dict(row) for row in self.state.get("stage_images", [])]
            best = S.head_of_checkpoint(self.state, self.state.get("best_checkpoint"), self.fingerprint_of)
            stored = {name: row.get("id") for name, row in self.readings().items()}
        by_key = {(r["call_id"], r["output_index"], r["sha256"]): n for n, r in enumerate(records)}
        matched = {}
        for image in images:
            key = (image["call_id"], image["part_index"], image["sha256"])
            if key in by_key:
                matched[image_key(image)] = by_key[key]
        latest = {}
        for n in sorted(set(matched.values())):
            rec = records[n]
            latest[rec["context_role"], rec["view"]] = rec["call_id"]
        out = {}
        for key, n in matched.items():
            rec = records[n]
            role = rec["context_role"]
            if role == "source" or best is not None and rec.get("revision") == best["revision"]:
                continue
            if role == "pick":
                if stored.get(rec.get("reading")) == f"{rec.get('ref')}-{rec.get('reading')}":
                    continue
            elif latest[role, rec["view"]] == rec["call_id"]:
                continue
            out[key] = (f"[superseded {role} image: call {rec['call_id']}, checkpoint {rec['checkpoint']}, "
                        f"revision {rec.get('revision')}, view {rec['view']}; path {rec['path']}]")
        return out


class StageBridge:
    """Submit inference on its owning loop while a background worker holds the parent scene lock.

    Cancellation covers the race before the child starts and waits for its actual unwind, not just cancellation of
    concurrent.futures.Future, before the caller closes the lab worker.
    """

    def __init__(self, loop):
        self.loop = loop
        self.lock = threading.Lock()
        self.cancelled = False
        self.started = threading.Event()
        self.done = threading.Event()
        self.future = None

    def cancel(self):
        with self.lock:
            self.cancelled = True
            if self.future is not None and self.started.is_set():
                self.future.cancel()

    def check(self):
        with self.lock:
            if self.cancelled:
                raise concurrent.futures.CancelledError("temple stage was cancelled")

    def run(self, runtime, spec, lab, cap_usd, call_id):
        async def child():
            try:
                self.started.set()
                with self.lock:
                    if self.cancelled:
                        raise asyncio.CancelledError
                return await runtime.run_stage(spec, lab, cap_usd, call_id)
            finally:
                self.done.set()

        self.check()
        future = asyncio.run_coroutine_threadsafe(child(), self.loop)
        with self.lock:
            self.future = future
            if self.cancelled and self.started.is_set():
                future.cancel()
        try:
            return future.result()
        finally:
            self.done.wait()


def run(scene, runtime, bridge, call_id, cap_usd, child_settings):
    """Called under the parent's scene lock. Only a completed, accepted child can apply a fitted spec."""
    from ..loop import StageSpec
    from ..procs import write_json_atomic
    from . import package
    from .cli import check_package, instructions_text, references, tool_contract

    bridge.check()
    folder = scene.root / "work" / "temple_stage" / hashlib.sha256(call_id.encode()).hexdigest()[:16]
    prepared = package.prepare(scene, folder, call_id)
    bridge.check()
    source = prepared["source"]
    lab = StageLab(folder)
    try:
        check_package(folder, lab)
        lab.start()
        # Briefs are bound data: preserve CRLF, lone CR and UTF-8 bytes exactly. The instruction
        # contract retains its existing canonical text path through instructions_text above.
        spec = StageSpec("temple_stage", instructions_text(folder, child_settings.finish_k),
                         registry(lab, child_settings), (folder / "inputs/brief.txt").read_bytes().decode("utf-8"),
                         child_settings, photos=references(lab),
                         scene_note=(f"Current job head captured in revision 0 at {lab.s.head['id']}. "
                                     f"This temple stage has a ${cap_usd} allowance inside the job's approval. "
                                     "The tool footer reports the shared job's totals; both limits apply."),
                         contract=tool_contract(),
                         contract_brief=(folder / "inputs/contract-brief.txt").read_bytes().decode("utf-8"))
        outcome = bridge.run(runtime, spec, lab, cap_usd, call_id)
        write_json_atomic(folder / "stage-outcome.json", outcome)
        bridge.check()
        finished = lab.state.get("finish")
        if outcome["status"] != "completed" or not finished:
            return {"status": "refused", "code": "E_STAGE_INCOMPLETE", "stage": outcome,
                    "message": "temple stage ended without an accepted finish; parent geometry is unchanged"}
        exit_head = outcome.get("exit_checkpoint") or {}
        if (exit_head.get("verified") is not True or exit_head.get("id") != lab.s.head["id"]
                or exit_head.get("sha256") != lab.s.head["sha256"]
                or finished["checkpoint"] != lab.s.head["id"]
                or finished.get("status") not in ("matched", "stuck")
                or (finished.get("validate_spec") or {}).get("ok") is not True
                or sha(finished["export"]["path"]) != finished["export"]["sha256"]):
            raise ValueError("accepted temple export no longer matches the verified child head")
        if (scene.s.head["id"] != source["checkpoint"] or scene.s.fingerprint() != source["fingerprint"]):
            return {"status": "refused", "code": "E_STAGE_STALE", "stage": outcome,
                    "message": "the parent head changed since the temple stage began"}
        fitted = lab.spec(finished["revision"])
        bridge.check()
        applied = scene.s.call_mutating("temple_stage", "temple_stage_apply",
                                        {"spec": fitted, "source_fingerprint": source["fingerprint"]},
                                        deadline_s=150, label=f"temple stage revision {finished['revision']}",
                                        call_id=call_id)
        # If cancellation races the final worker mutation, the parent journal records it and the existing
        # interrupted-call recovery restores checkpoint_before. No cancelled call can become a finished delivery.
        bridge.check()
        result = {**applied, "stage": outcome, "package": folder.relative_to(scene.root).as_posix(),
                  "source": source, "revision": finished["revision"], "summary": finished.get("summary"),
                  "finish": finished["status"]}
        write_json_atomic(folder / "application.json", result)
        return result
    finally:
        lab.close()
