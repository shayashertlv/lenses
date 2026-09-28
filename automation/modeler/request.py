"""The job request: image paths, optional view labels and dimensions, run limits, the author driver.

A request is data the job copies verbatim into its folder. Nothing in it can grant credentials or raise the paid
cap (those come from the command line and a ledger).
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path

VIEWS = ("front", "back", "left", "right", "angled", "top", "rear_angled", "other", "unknown")   # the code fits cameras for the first five
DEFAULT_LIMITS = {"max_turns": 10, "blender_time_limit_s": 300, "wall_time_limit_min": 180,
                  "stagnation_turns": 3, "render_scale": 0.5}
DEFAULT_FRONT_WIDTH_MM = 140.0
DEFAULT_WIDTH_UNCERTAINTY = 0.10        # relative, when the width is assumed rather than stated


@dataclass
class Photo:
    path: Path
    view: str = "unknown"
    held_out: bool = False


@dataclass
class Request:
    product_id: str
    photos: list[Photo]
    dimensions: dict = field(default_factory=dict)
    notes: str = ""
    limits: dict = field(default_factory=lambda: dict(DEFAULT_LIMITS))
    author: dict = field(default_factory=lambda: {"driver": "package"})
    held_out_views: tuple[str, ...] = ("angled",)
    donor: dict | None = None
    raw: dict = field(default_factory=dict)

    @staticmethod
    def load(path: Path) -> "Request":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return Request.from_dict(raw, base=Path(path).parent)

    @staticmethod
    def from_dict(raw: dict, base: Path | None = None) -> "Request":
        if not isinstance(raw, dict) or not isinstance(raw.get("photos"), list) or len(raw["photos"]) < 2:
            raise ValueError("A request needs a product_id and at least two photos")
        pid = str(raw.get("product_id") or "").strip()
        if not pid or not all(c.isalnum() or c in "-_" for c in pid):
            raise ValueError("product_id must be a short [A-Za-z0-9_-] token")
        held = tuple(raw.get("held_out_views") or ("angled",))
        photos = []
        for p in raw["photos"]:
            if isinstance(p, str):
                p = {"path": p}
            path = Path(p["path"])
            if base is not None and not path.is_absolute():
                path = (base / path).resolve()
            if not path.is_file():
                raise FileNotFoundError(f"Photo not found: {path}")
            view = str(p.get("view", "unknown"))
            if view not in VIEWS:
                raise ValueError(f"Unknown view label {view!r}; use one of {VIEWS}")
            photos.append(Photo(path, view, bool(p.get("held_out", view in held))))
        views = [p.view for p in photos if p.view not in ("unknown", "other")]
        if len(views) != len(set(views)):
            raise ValueError("Each view label may be used once")
        if all(p.held_out for p in photos):
            raise ValueError("At least one photo must be available to the author")
        dims = dict(raw.get("dimensions") or {})
        for k, v in dims.items():
            if v is not None and (not isinstance(v, (int, float)) or not 1 < v < 400):
                raise ValueError(f"dimension {k} must be a millimetre number or null")
        limits = dict(DEFAULT_LIMITS)
        limits.update(raw.get("limits") or {})
        author = dict(raw.get("author") or {"driver": "package"})
        return Request(pid, photos, dims, str(raw.get("notes") or ""), limits, author, held, raw.get("donor"), raw)

    def front_width_mm(self) -> tuple[float, dict]:
        """(width, provenance): the stated front width or the assumed default with its uncertainty."""
        w = self.dimensions.get("front_width_mm")
        if w:
            return float(w), {"source": "stated_in_request", "uncertainty_mm": float(self.dimensions.get("front_width_uncertainty_mm", 2.0))}
        return DEFAULT_FRONT_WIDTH_MM, {"source": "assumed_default", "uncertainty_mm": DEFAULT_FRONT_WIDTH_MM * DEFAULT_WIDTH_UNCERTAINTY,
                                        "note": "no physical dimension supplied; every millimetre in this job is nominal"}

    def to_dict(self) -> dict:
        return {"product_id": self.product_id,
                "photos": [{"path": str(p.path), "view": p.view, "held_out": p.held_out} for p in self.photos],
                "dimensions": self.dimensions, "notes": self.notes, "limits": self.limits, "author": self.author,
                "held_out_views": list(self.held_out_views), "donor": self.donor}
