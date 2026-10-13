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


def _parts(job: dict) -> list[list[tuple[bool, str]]]:
    """One source of host prose and verbatim owner data; True marks host-authored text."""
    def host(text):
        return [(True, text)]

    lines = [[(True, "Product: "), (False, job["name"])], host("Owner's job description (verbatim):"),
             [(False, job["description"])], host("Owner's specifications (verbatim):"),
             [(False, job["specs_json"])],
             host("Every reference image below is an authoritative photo of this product."),
             host("Photos determine shape and colour. The description and specifications supply facts a photo cannot "
                  "show, such as exact sizes and materials. Report conflicts explicitly."),
             host("Owner's model requirements:"), host(MODEL_REQUIREMENTS), host("Reference photos:")]
    for index, row in enumerate(job["images"]):
        side = row["side"] if row["side"] is not None else "not specified"
        w, h = row["size_px"]
        lines.append([(True, f"Reference {index + 1} of {len(job['images'])}: view '"),
                      (False, row["view_kind"]), (True, "', side '"), (False, side), (True, "', "),
                      (False, row["file"]), (True, f", {w}×{h} px, sha256 "), (False, row["sha256"])])
    return lines


def build_brief(job: dict) -> str:
    """Include description and original specs JSON without normalization or paraphrase."""
    return "\n\n".join("".join(text for _, text in line) for line in _parts(job))


def host_text(job: dict) -> str:
    """Contract-check our prose without censoring the owner's names, files or exact words.

    The complete build_brief output, including owner data, still participates in approval binding.
    """
    return "\n\n".join("".join(text if authored else "[owner data]" for authored, text in line)
                       for line in _parts(job))
