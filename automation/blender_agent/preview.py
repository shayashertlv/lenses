"""Native scene export and images from the repository's actual AR renderer.

These are observation tools for the SDK agent, not a second editing pipeline.
Only temporary export copies are normalized; live geometry/materials stay editable.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import math
from pathlib import Path
import uuid
from typing import Literal

from agents import function_tool
from agents.tool import ToolOutputImage, ToolOutputText
from pydantic import BaseModel, Field

from bsa import archeck

from .evidence_json import EvidenceJSONRegistry


class ARView(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,39}$")
    yaw_degrees: float = Field(default=0, ge=-80, le=80)
    pitch_degrees: float = Field(default=0, ge=-60, le=60)
    roll_degrees: float = Field(default=0, ge=-60, le=60)
    type: Literal["pose", "asset-back"] = "pose"


def validate_preview_structure(ar: dict) -> dict:
    """Preserve render validation while rejecting unmeasured/failed structural evidence."""
    validation = copy.deepcopy(ar.get("render_validation", ar.get("validation", {"ok": False})))
    models = validation.setdefault("models", {})
    for name, row in ar.get("models", {}).items():
        previous = models.get(name, {"ok": validation.get("ok") is True, "reasons": []})
        reasons = list(previous.get("reasons", []))
        if row.get("continuity_measured") is not True:
            reasons.append("Temple continuity was not measured")
        elif row.get("continuity_failure") is not None:
            reasons.append(f"Temple continuity failed: {str(row['continuity_failure'])[:1000]}")
        if row.get("synthetic_fit_ready") is not True:
            reasons.append("Synthetic fitting did not settle" if row.get("synthetic_fit_ready") is False
                           else "Synthetic fitting was not measured")
        models[name] = {"ok": previous.get("ok") is True and not reasons,
                        "reasons": list(dict.fromkeys(reasons))}
    validation["ok"] = (validation.get("ok") is True and bool(ar.get("models"))
                        and all(row.get("ok") is True for row in models.values()))
    return validation


def compact_ar_result(result: dict) -> dict:
    """The model sees observations and evidence locations, not repeated material/log dumps."""
    rows = result["ar"].get("models", {})
    return {"glb": result["glb"], "sha256": result["sha256"], "metadata_path": result["metadata_path"],
            "validation": validate_preview_structure(result["ar"]),
            "render_validation": result["ar"].get("render_validation", result["ar"].get("validation", {"ok": False})),
            "diagnostics": {name: {"status": row.get("status"), "error": row.get("error"),
                "optical_meshes_detected": row.get("optical_meshes_detected"),
                "continuity_measured": row.get("continuity_measured") is True,
                "continuity_failure": row.get("continuity_failure"),
                "fitting": {"measured": isinstance(row.get("synthetic_fit_ready"), bool),
                            "ready": row.get("synthetic_fit_ready")}}
                            for name, row in rows.items()},
            "scene_unchanged": result["export"].get("scene_unchanged") is True,
            "captures": [{"view": r.get("view"), "path": r.get("path"), "sha256": r.get("sha256")}
                         for row in rows.values() for r in row.get("render_files", [])],
            "omitted_from_response": {"native_materials": len(result.get("native_materials", [])),
                                      "exported_objects": len(result["export"].get("objects", [])),
                                      "details": "Full materials, export receipt and harness diagnostics are saved in metadata_path"},
            "scope": "Synthetic pose/background. Render completion is separate from structural compatibility; neither certifies visual quality."}


def verified_image(path: str, expected_sha: str | None, output: Path) -> Path | None:
    """Failed compatibility may still produce useful images; only forward pinned local bytes."""
    try:
        candidate = Path(path).resolve(strict=True)
        if (not candidate.is_relative_to(output.resolve()) or candidate.suffix.lower() != ".png"
                or not candidate.is_file() or candidate.stat().st_size > 20 * 1024 * 1024):
            return None
        if hashlib.sha256(candidate.read_bytes()).hexdigest() == expected_sha:
            return candidate
    except (OSError, ValueError):
        pass
    return None


class ARPreview:
    def __init__(self, server, output: Path, *, meters_per_unit: float = .001,
                 origin: tuple[float, float, float] | None = None,
                 evidence_registry: EvidenceJSONRegistry | None = None):
        if not math.isfinite(meters_per_unit) or meters_per_unit <= 0:
            raise ValueError("meters_per_unit must be finite and positive")
        self.server, self.output = server, output.resolve()
        self.meters_per_unit, self.origin = meters_per_unit, origin
        self.evidence_registry = evidence_registry or EvidenceJSONRegistry(self.output)

    async def capture(self, label: str, views: list[ARView], background: str = "checker",
                      background_color: str | None = None) -> dict:
        if not 1 <= len(views) <= 6 or len({view.id for view in views}) != len(views):
            raise ValueError("Provide one to six views with distinct IDs")
        if background not in {"checker", "solid"}:
            raise ValueError("background must be checker or solid")
        for view in views:
            if view.type == "asset-back" and any((view.yaw_degrees, view.pitch_degrees, view.roll_degrees)):
                raise ValueError("asset-back is an asset inspection; leave its pose angles at zero")
        if background_color is not None and background != "solid":
            raise ValueError("background_color applies only to solid")
        directory, glb, receipt = await self.export_current(label)
        result = await asyncio.to_thread(
            archeck.run, {"candidate": glb}, directory / "preview",
            ar_views=[view.model_dump() for view in views], background=background,
            background_color=background_color,
            description="Unmodified native Blender glTF, full lens solids and current materials. "
                        "Only export copies have world transforms, units and bridge origin baked.",
        )
        result["render_validation"] = copy.deepcopy(result.get("validation", {"ok": False}))
        result["validation"] = validate_preview_structure(result)
        result["all_compatible_with_lenses"] = result["validation"]["ok"]
        return self.save_result(directory, glb, receipt, {"ar": result})

    async def export_current(self, label: str) -> tuple[Path, Path, dict]:
        """Shared exact native export, independently of the observation background."""
        directory = self.output / "ar" / (archeck.safe_id(label) + "-" + uuid.uuid4().hex[:8])
        directory.mkdir(parents=True, exist_ok=False)
        glb = directory / "model.glb"
        from .native_export import RECEIPT_PREFIX, export_code
        response = await self.server.call_tool("execute_blender_code", {
            "code": export_code(glb, meters_per_unit=self.meters_per_unit, origin=self.origin),
            "user_prompt": "Export the current persistent scene for an actual AR preview without changing it",
        })
        text = "\n".join(block.text for block in response.content if block.type == "text")
        (directory / "export-response.txt").write_text(text, encoding="utf-8")
        if response.isError or not glb.is_file() or glb.stat().st_size < 20:
            raise RuntimeError(f"Native export failed; {directory / 'export-response.txt'}\n{text[-6000:]}")
        receipts = [json.loads(line.split(RECEIPT_PREFIX, 1)[1]) for line in text.splitlines()
                    if line.startswith(RECEIPT_PREFIX)]
        if len(receipts) != 1 or receipts[0].get("status") != "exported" or receipts[0].get("scene_unchanged") is not True:
            raise RuntimeError(f"Export did not confirm success and an unchanged live scene: {directory / 'export-response.txt'}")
        receipt = receipts[0]
        (directory / "export-receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
        return directory, glb, receipt

    def save_result(self, directory: Path, glb: Path, receipt: dict, observation: dict) -> dict:
        from .inspect_export import glb_summary
        native = glb_summary(glb)
        (directory / "native-glb.json").write_text(json.dumps(native, indent=2), encoding="utf-8")
        result = {"glb": str(glb), "sha256": hashlib.sha256(glb.read_bytes()).hexdigest(),
                  "directory": str(directory), "export": receipt, **observation,
                  "native_materials": native["materials"], "extensions_used": native["extensions_used"],
                  "metadata_path": str(directory / "preview-result.json")}
        Path(result["metadata_path"]).write_text(json.dumps(result, indent=2), encoding="utf-8")
        # Fixed producer-owned paths only; never follow arbitrary paths found in JSON.
        for relative in ("preview-result.json", "native-glb.json", "export-receipt.json",
                         "preview/report.json", "preview/manifest.json", "portrait/report.json",
                         "portrait/manifest.json", "portrait/portrait-result.json"):
            path = directory / relative
            if path.is_file():
                self.evidence_registry.register(path, "preview")
        return result

    async def capture_portrait(self, label: str, glb_path: str) -> dict:
        from .portrait import run_portrait
        glb = Path(glb_path).resolve()
        if not glb.is_relative_to(self.output) or glb.suffix.lower() != ".glb" or not glb.is_file():
            raise ValueError("Use an existing .glb inside this agent's output directory; export with preview_ar first")
        directory = self.output / "portrait" / (archeck.safe_id(label) + "-" + uuid.uuid4().hex[:8])
        directory.mkdir(parents=True, exist_ok=False)
        portrait = await asyncio.to_thread(run_portrait, glb, directory / "portrait")
        return self.save_result(directory, glb, {"live_scene_accessed": False, "source_glb_reused": True}, {"portrait": portrait})

    def tool(self):
        @function_tool
        async def preview_ar(label: str, views: list[ARView],
                             background: Literal["checker", "solid"] = "checker",
                             background_color: str | None = None) -> list[ToolOutputText | ToolOutputImage]:
            """Export the CURRENT live scene and view that exact GLB in the actual AR engine.

            Preserves full lens geometry and native exportable materials; no canonical
            lens conversion or material-record reconstruction. Uses current runtime
            lighting and a synthetic pose/background, not a wearer photograph.
            Choose 1-6 views: yaw +/-80, pitch/roll +/-60; asset-back inspects the rear.
            Returns pinned image blocks even when compatibility fails, plus exact
            continuity/fit diagnostics. Rendering does not establish structural or
            visual quality. Use read_evidence_json on metadata_path for more detail.
            This export requires +Y up, +Z forward; unit/origin are host-configured.
            Render-hidden objects are omitted; viewport isolation does not omit parts.
            Arbitrary closer inspection is also available inside Blender through MCP.
            """
            from .agent import image_url
            result = await self.capture(label, views, background, background_color)
            outputs = [ToolOutputText(text=json.dumps(compact_ar_result(result), ensure_ascii=False))]
            rows = result["ar"].get("models", {})
            for row in rows.values():
                for render in row.get("render_files", []):
                    path = verified_image(render.get("path", ""), render.get("sha256"), self.output)
                    if path is not None:
                        outputs.extend([ToolOutputText(text=f"Actual AR: {render['view']} — {path}"),
                                        ToolOutputImage(image_url=image_url(path), detail="high")])
            return outputs

        return preview_ar

    def portrait_tool(self):
        @function_tool
        async def preview_portrait(label: str, glb_path: str) -> list[ToolOutputText | ToolOutputImage]:
            """Inspect an EXISTING exact exported GLB on a fixed synthetic wearer, without touching Blender.

            Returns real AR images, including native-pixel eye details, under two
            host-controlled observation conditions. The synthetic wearer is supplementary
            evidence, not a target product reference. Uses only its detected pose, not
            invented head turns. Repeat before/after edits for matched visual evidence.
            glb_path must be a .glb already inside this agent's output directory, such
            as preview_ar's glb path. No live camera or paid API is used. Full
            root diagnostics are returned directly; read_evidence_json can inspect
            the full saved report. Existing pinned images remain available after failure.
            """
            from .agent import image_url
            result = await self.capture_portrait(label, glb_path)
            portrait = result["portrait"]
            captures = [c for c in portrait.get("captures", []) if c.get("send_to_agent") is True]
            summary = {"glb": result["glb"], "sha256": result["sha256"], "metadata_path": result["metadata_path"],
                       "validation": portrait["validation"], "comparison_key": portrait.get("comparison_key"),
                       "diagnostics": portrait.get("diagnostics", {}),
                       "live_scene_accessed": False,
                       "scope": "Synthetic wearer; supplementary evidence, not a product reference. Same detected pose and framing.",
                       "captures": [{k: c[k] for k in ("label", "path", "sha256", "width", "height")} for c in captures],
                       "omissions": portrait["omissions"] + ["Full material/lighting/fit diagnostics and additional full view saved on disk"]}
            outputs = [ToolOutputText(text=json.dumps(summary, ensure_ascii=False))]
            for capture in captures:
                path = verified_image(capture.get("path", ""), capture.get("sha256"), self.output)
                if path is not None:
                    outputs.extend([ToolOutputText(text=capture["label"]),
                                    ToolOutputImage(image_url=image_url(path), detail="high")])
            return outputs

        return preview_portrait
