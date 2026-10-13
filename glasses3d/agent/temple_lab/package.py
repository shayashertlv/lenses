"""A private temple lab captured from the CURRENT parent job head; no product defaults."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import shutil

from PIL import Image, ImageOps

from glasses3d.g3d_host import compare as C
from . import cli, pose_basis
from .lab import LAB_SCHEMA, Lab, read, sha, write_json_atomic


def photo_catalog(job: dict) -> list[dict]:
    """Pure IDs/roles shared by preflight and execution; no filename prefix is stripped.

    Current intake supplies original_name, view_kind and side: all such rows are targets.
    Explicit id/basis_ref/role/colourway/colour/finish metadata is preserved when provided.
    """
    out = []
    for row in job.get("images") or []:
        name = row.get("original_name")
        if not isinstance(name, str) or not name or Path(name).name != name:
            raise ValueError("every stage photo needs its original filename")
        rid = row.get("basis_ref", C.ref_id(name))
        pid = row.get("id", C.ref_id(name))
        if any(not isinstance(v, str) or not v.strip() or any(c in v for c in ("/", "\\", "\n", "\r"))
               for v in (rid, pid)):
            raise ValueError("photo id and basis_ref must be explicit nonempty names")
        colourway = row.get("colourway") or "target"
        role = row.get("role", "target" if colourway == "target" else "colourway")
        if role not in ("target", "colourway", "reference"):
            raise ValueError(f"{pid}: unknown photo role")
        out.append({"id": pid, "basis_ref": rid, "role": role, "original_name": name,
                    "view": row["view_kind"], "side": row.get("side"), "colourway": colourway,
                    **{k: deepcopy(row[k]) for k in ("colour", "finish") if k in row}})
    if not out or not any(row["role"] == "target" for row in out):
        raise ValueError("a temple stage needs target photos")
    for key in ("id", "basis_ref", "original_name"):
        if len({r[key].casefold() for r in out}) != len(out):
            raise ValueError(f"stage photo {key} values must be distinct")
    return out


def contract_brief(photos: list[dict], cameras: dict, rig: str) -> str:
    """The host-written brief template with neutral labels, separate from verbatim owner data.

    The contract checks instructions emitted by the host. Product names, photo IDs, descriptions,
    specifications, colours, known conflicts and reading observations are bound data, not instructions.
    """
    neutral, calibrated = [], {}
    for index, row in enumerate(photos):
        name = f"photo-{index:03d}.png"
        neutral.append({"id": f"photo-{index:03d}", "path": f"images/{name}", "view": "front",
                        "role": row["role"], **({"colour": "alternate"} if row["role"] != "target" else {})})
        source = cameras.get(Path(row["path"]).name) or {}
        calibrated[name] = {k: source[k] for k in ("camera", "theta_deg", "hinge_axis_xz_mm") if k in source}
        provenance = source.get("pose_provenance") or {}
        if provenance.get("mode") == "as_authored" and provenance.get("opening_calibrated") is False:
            calibrated[name]["pose_provenance"] = {"mode": "as_authored", "opening_calibrated": False}
    return cli.brief_text("Eyewear", neutral, calibrated, rig=rig)


def prepare(scene, destination, call_id: str, *, lab_class=Lab) -> dict:
    """Capture the current parent and prepare revision zero on a closed private Lab.

    The caller holds its scene lock. Parent state is checked before and after preparation.
    A failed preparation keeps its diagnostic files, but can never be reused as prepared.
    """
    root, parent_root = Path(destination).resolve(), Path(scene.root).resolve()
    if root.exists():
        raise ValueError("use a new empty stage package directory")
    catalog = photo_catalog(scene.job)
    head = deepcopy(scene.s.head)
    if not head or scene.s.fingerprint() != head["fingerprint"]:
        raise ValueError("parent live scene differs from its current checkpoint")
    checkpoint = parent_root / "checkpoints" / head["file"]
    if sha(checkpoint) != head["sha256"]:
        raise ValueError("parent checkpoint bytes changed")
    basis = C.load_basis(parent_root)
    basis_sha = sha(basis.dir / "basis.json")
    exp = scene.seeing.export_head(call_id + "-source")
    if (exp.get("status") != "exported" or exp.get("drift") or exp.get("checkpoint") != head["id"]
            or exp.get("fingerprint") != head["fingerprint"] or sha(exp["path"]) != exp.get("sha256")):
        raise ValueError("parent export does not match its current checkpoint")
    inputs = root / "inputs"
    (inputs / "images").mkdir(parents=True)
    assets = []

    def copy(src, rel, expected):
        dst = (inputs / rel).resolve()
        if not dst.is_relative_to(inputs) or sha(src) != expected:
            raise ValueError(f"{rel}: source escaped or changed")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        if sha(dst) != expected or sha(src) != expected:
            raise ValueError(f"{rel}: copy differs from its source")
        assets.append({"path": rel, "sha256": expected})
        return dst

    copy(checkpoint, "scene.blend", head["sha256"])
    copy(exp["path"], "parent.glb", exp["sha256"])
    photos = []
    for row, info in zip(scene.job["images"], catalog):
        src = (parent_root / row["file"]).resolve()
        if not src.is_relative_to(parent_root):
            raise ValueError("photo source escaped the parent job")
        rel = "images/" + row["original_name"]
        dst = copy(src, rel, row["sha256"])
        with Image.open(dst) as im:
            size = list(ImageOps.exif_transpose(im).size)
        photos.append({**info, "path": rel, "size_px": size, "sha256": row["sha256"]})
    private_basis = pose_basis.build(basis, root, photos, call_id=call_id,
                                    captured_glb=inputs / "parent.glb", captured_sha256=exp["sha256"])
    source = {"job_root": str(parent_root), "checkpoint": head["id"], "checkpoint_sha256": head["sha256"],
              "fingerprint": head["fingerprint"], "export_file": "parent.glb", "export_sha256": exp["sha256"]}
    bound_basis = {"parent_id": basis.id, "source": str(basis.dir / "basis.json"), "source_sha256": basis_sha,
                   "id": private_basis.id, "files": cli.basis_files(root, private_basis.id)}
    rig = scene.job.get("rig") or cli.DEFAULT_RIG
    if rig not in cli.MATCHED_RIGS:
        raise ValueError("unknown temple-stage render rig")
    cameras = read(private_basis.dir / C.CAMERAS_JSON)["images"]
    brief = cli.brief_text(scene.job.get("name") or "Job", photos, cameras, rig=rig,
                           conflicts=scene.job.get("camera_conflicts") or (),
                           precision=scene.job.get("reading_precision"))
    brief += "\nOwner description:\n" + scene.job.get("description", "") + "\n"
    if scene.job.get("specs") is not None:
        brief += "Owner specifications:\n" + scene.job.get("specs_json", str(scene.job["specs"])) + "\n"
    (inputs / "brief.txt").write_text(brief, encoding="utf-8")
    assets.append({"path": "brief.txt", "sha256": sha(inputs / "brief.txt")})
    (inputs / "contract-brief.txt").write_text(contract_brief(photos, cameras, rig), encoding="utf-8")
    assets.append({"path": "contract-brief.txt", "sha256": sha(inputs / "contract-brief.txt")})
    copy(cli.HERE / "instructions.txt", "instructions.txt", sha(cli.HERE / "instructions.txt"))
    write_json_atomic(inputs / "lab.json", {"schema": LAB_SCHEMA, "rig": rig, "product": scene.job.get("name"),
                                             "assets": assets, "photos": photos, "source": source,
                                             "basis": bound_basis})
    job = {"schema": "g3d.job/1", "name": f"{scene.job.get('name')}: temple stage", "rig": rig,
           "implementation": cli.implementation(), "scope": "private stage on the captured current parent head"}
    write_json_atomic(root / "job.json", job)
    out = cli.finish_preparation(root, private_basis.id, job, photo_count=len(photos), lab_class=lab_class)
    if (scene.s.head != head or scene.s.fingerprint() != head["fingerprint"]
            or sha(checkpoint) != head["sha256"] or sha(basis.dir / "basis.json") != basis_sha):
        (root / "prepared.json").unlink(missing_ok=True)
        raise ValueError("parent scene or frozen basis changed during stage preparation")
    target = [r["id"] for r in photos if r["role"] == "target"]
    compared = target + [r["id"] for r in photos if r["role"] == "colourway"]
    return {**out, "source": source, "basis": bound_basis, "photo_ids": target, "compare_ids": compared,
            "image_ids": compared + [r["id"] for r in photos if r["role"] == "reference"]}
