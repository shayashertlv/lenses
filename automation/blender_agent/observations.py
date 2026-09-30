"""Image inspection and short, evidence-linked artist review notes."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Literal
import uuid

from agents import function_tool
from agents.tool import ToolOutputImage, ToolOutputText
from PIL import Image, ImageOps
from pydantic import BaseModel, Field, model_validator


class ImageRegion(BaseModel):
    left: float = Field(ge=0, le=1)
    top: float = Field(ge=0, le=1)
    right: float = Field(ge=0, le=1)
    bottom: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def ordered(self):
        if self.right <= self.left or self.bottom <= self.top:
            raise ValueError("Region must have positive width and height")
        return self


def evidence_path(path: str, output: Path, references: list[Path]) -> Path:
    candidate = Path(path).resolve(strict=True)
    if not candidate.is_file() or (not candidate.is_relative_to(output.resolve())
                                  and candidate not in {p.resolve() for p in references}):
        raise ValueError("Evidence must be inside the output directory or an explicit reference file")
    return candidate


def inspect_image(path: str, output: Path, references: list[Path], region: ImageRegion | None = None):
    from .agent import image_url
    source = evidence_path(path, output, references)
    if source.stat().st_size > 20 * 1024 * 1024:
        raise ValueError("Image exceeds 20 MiB")
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    with Image.open(source) as original:
        if original.format not in {"PNG", "JPEG", "WEBP", "GIF"}:
            raise ValueError("Expected a PNG, JPEG, WebP, or GIF image")
        oriented = ImageOps.exif_transpose(original)
        width, height = oriented.size
        metadata = {"source": str(source), "source_sha256": source_hash,
                    "source_size": [width, height], "region": None,
                    "note": "Original evidence; no enhancement or invented detail."}
        if region is None:
            shown = source
        else:
            box = (round(region.left * width), round(region.top * height),
                   round(region.right * width), round(region.bottom * height))
            if box[2] - box[0] < 2 or box[3] - box[1] < 2:
                raise ValueError("Crop must contain at least two source pixels in each dimension")
            key = hashlib.sha256((source_hash + repr(box)).encode()).hexdigest()[:24]
            directory = output.resolve() / "observations"
            directory.mkdir(parents=True, exist_ok=True)
            shown = directory / f"crop-{key}.png"
            oriented.crop(box).save(shown)
            metadata.update(region=region.model_dump(), source_pixel_box=list(box),
                            displayed_size=[box[2] - box[0], box[3] - box[1]])
        metadata["displayed_image"] = str(shown)
        if region is not None:
            shown.with_suffix(".json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return [ToolOutputText(text=json.dumps(metadata)), ToolOutputImage(image_url=image_url(shown), detail="high")]


def make_read_image(output: Path, references: list[Path]):
    @function_tool
    def read_image(path: str, region: ImageRegion | None = None) -> list[ToolOutputText | ToolOutputImage]:
        """Inspect an original reference or saved render, optionally at a selected detail.

        Region uses normalized left/top/right/bottom coordinates from 0 to 1.
        The result includes original dimensions and an unenhanced crop at native
        resolution. Use several source views for ambiguous details; enlarging a
        low-resolution source does not supply missing evidence.
        """
        return inspect_image(path, output, references, region)
    return read_image


class VisualFinding(BaseModel):
    region: str = Field(min_length=1, max_length=160)
    observation: str = Field(min_length=1, max_length=700)
    interpretation: str = Field(min_length=1, max_length=700)
    alternative: str | None = Field(default=None, max_length=500)
    evidence: list[str] = Field(min_length=1, max_length=6)
    outcome: Literal["unresolved", "improved", "regressed", "verified", "uncertain"]
    next_check: str | None = Field(default=None, max_length=500)


class ComparisonProgress(BaseModel):
    focus: str = Field(min_length=1, max_length=160)
    product_result: Literal["improved", "regressed", "unchanged", "uncertain"]
    new_information: bool
    comparison_basis: str = Field(min_length=1, max_length=500)
    next_test: str | None = Field(default=None, max_length=500)


def progress_advice(progress: ComparisonProgress | None, directory: Path) -> dict:
    """Flag repeated artist-reported dead ends, never infer quality from mesh hashes."""
    advice = {"assessment_source": "agent_reported", "automatic_stop": False,
              "repeated_no_gain": False}
    if progress is None:
        return advice
    previous = None
    latest_time = float("-inf")
    focus = lambda item: " ".join(item.focus.casefold().split())
    for path in directory.glob("*.json"):
        if path.is_symlink() or not path.resolve().is_relative_to(directory.resolve()):
            continue
        try:
            if path.stat().st_size > 128 * 1024:
                continue
            report = json.loads(path.read_text(encoding="utf-8"))
            if report.get("progress") is not None:
                candidate = ComparisonProgress.model_validate(report["progress"])
                if focus(candidate) != focus(progress):
                    continue
                created_at = datetime.fromisoformat(report["created_at"])
                if created_at.tzinfo is None:
                    continue
                timestamp = created_at.timestamp()
                if timestamp > latest_time:
                    latest_time, previous = timestamp, candidate
        except (OSError, ValueError, TypeError, AttributeError, KeyError, OverflowError):
            continue
    no_gain = lambda item: (item.product_result in {"unchanged", "regressed"}
                            and not item.new_information)
    if previous is not None and focus(previous) == focus(progress) and no_gain(previous) and no_gain(progress):
        advice.update(repeated_no_gain=True, action=
                      "Two comparisons on this focus report neither improvement nor new information. "
                      "Do not repeat an equivalent attempt. Test a different discriminating hypothesis, "
                      "or retain/revert the best reviewed candidate and report the limitation. "
                      "Complete necessary rollback, export and validation; this is not quality acceptance.")
    return advice


def make_record_review(output: Path, references: list[Path], *, evidence_registry=None):
    @function_tool
    def record_review(label: str, findings: list[VisualFinding],
                      checkpoint: str | None = None, glb: str | None = None,
                      progress: ComparisonProgress | None = None) -> dict:
        """Save a concise visual assessment tied to the exact evidence inspected.

        Record visible observations, brief physical interpretations, unresolved
        alternatives and the next useful check. These are artist judgments, not
        automatic quality certification. Use after meaningful comparisons, not
        every tool call. Evidence paths must be existing images in the supplied
        references/output; checkpoint and GLB must be existing output files.
        For a before/after experiment, add progress with the same focus label,
        matched camera/lighting basis, product result and whether you learned
        anything that changes the diagnosis. A no-change mesh is not convergence.
        """
        if not label.strip() or len(label) > 120 or not 1 <= len(findings) <= 8:
            raise ValueError("Use a short label and one to eight findings")
        identities = {}
        for finding in findings:
            for path in finding.evidence:
                item = evidence_path(path, output, references)
                if item.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
                    raise ValueError("Visual evidence must be an image")
                identities[str(item)] = hashlib.sha256(item.read_bytes()).hexdigest()
        artifacts = {}
        for kind, path, suffix in (("checkpoint", checkpoint, ".blend"), ("glb", glb, ".glb")):
            if path is not None:
                item = evidence_path(path, output, [])
                if item.suffix.lower() != suffix:
                    raise ValueError(f"{kind} must be a {suffix} file")
                artifacts[kind] = {"path": str(item), "sha256": hashlib.sha256(item.read_bytes()).hexdigest()}
        directory = output.resolve() / "reviews"
        advice = progress_advice(progress, directory)
        report = {"label": label, "created_at": datetime.now(timezone.utc).isoformat(),
                  "assessment_source": "agent_visual_judgment_not_automatic_certification",
                  "artifacts": artifacts, "image_sha256": identities,
                  "findings": [finding.model_dump() for finding in findings],
                  "progress": progress.model_dump() if progress is not None else None,
                  "progress_advice": advice}
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}.json"
        destination.write_text(json.dumps(report, indent=2), encoding="utf-8")
        if evidence_registry is not None:
            evidence_registry.register(destination, "review")
        return {"review": str(destination), "finding_count": len(findings),
                "unresolved": [finding.region for finding in findings
                               if finding.outcome in {"unresolved", "regressed", "uncertain"}],
                "quality_certified": False, "progress_advice": advice}
    return record_review
