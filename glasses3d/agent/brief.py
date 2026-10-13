"""The owner's exact job facts plus the shared model requirements; no product defaults."""
from __future__ import annotations

MODEL_REQUIREMENTS = """| Property | Requirement |
|---|---|
| Format | GLB; prefer one self-contained file with embedded textures and buffers |
| Units | real-world metres (a 145 mm frame ≈ 0.145 units across) |
| Origin | underside of the bridge at (0,0,0): the attachment and scaling reference |
| Orientation | +Y up, +Z forward, temples extend along −Z, arms on opposite sides of X |
| Transforms | baked into mesh coordinates; every mesh's effective transform relative to the root = identity |
| Geometry | finite vertices and indices; static, triangulated |
| Components | lenses clearly distinguished from frame and temples by their material assignments; no fixed mesh count or names |"""


def build_brief(job: dict) -> str:
    """Include description and original specs JSON without normalization or paraphrase."""
    lines = [f"Product: {job['name']}", "Owner's job description (verbatim):", job["description"],
             "Owner's specifications (verbatim):", job["specs_json"],
             "Every reference image below is an authoritative photo of this product.",
             "Photos determine shape and colour. The description and specifications supply facts a photo cannot "
             "show, such as exact sizes and materials. Report conflicts explicitly.",
             "Owner's model requirements:", MODEL_REQUIREMENTS, "Reference photos:"]
    for index, row in enumerate(job["images"]):
        side = row["side"] if row["side"] is not None else "not specified"
        w, h = row["size_px"]
        lines.append(f"Reference {index + 1} of {len(job['images'])}: view '{row['view_kind']}', side '{side}', "
                     f"{row['file']}, {w}×{h} px, sha256 {row['sha256']}")
    return "\n\n".join(lines)
