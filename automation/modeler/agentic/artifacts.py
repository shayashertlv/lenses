"""Immutable content-addressed files of a job, with host-enforced access roles.

Every photo, crop, program, render, GLB, log and report is written once to a fresh path
``<area>/<kind>/<id>__<sha256[:12]><suffix>`` (temporary sibling, flush, fsync, atomic promote), then catalogued in
the database with its SHA-256 and lineage. Nothing is ever overwritten. Areas: ``artifacts/`` (author-visible),
``sealed/`` (the held-out evidence: never readable by author tools, never mounted into a worker), ``host/`` (receipts,
raw responses, final records), ``worker/`` (bundles and ingested outputs), ``synthetic/`` (a fake worker's output,
conspicuously separate). Reading verifies the bytes against the catalogue; a caller names the roles it may read.
"""
from __future__ import annotations

import io
import os
from pathlib import Path
import stat
import uuid

from PIL import Image

from .state import Store, StateError, sha256_bytes

AUTHOR_VISIBLE = "author_visible"
SEALED = "sealed"
HOST_ONLY = "host_only"
WORKER_OUTPUT = "worker_output"
SYNTHETIC = "synthetic"
ROLES = (AUTHOR_VISIBLE, SEALED, HOST_ONLY, WORKER_OUTPUT, SYNTHETIC)
AREA = {AUTHOR_VISIBLE: "artifacts", SEALED: "sealed", HOST_ONLY: "host", WORKER_OUTPUT: "worker", SYNTHETIC: "synthetic"}
# request receipts (exact payload and response bytes) are bound by the inference_requests rows (payload_sha256 / response_sha256),
# not by the artifact catalogue: the orphan scan leaves them alone instead of listing every request of every resume
UNCATALOGUED_PREFIXES = ("host/requests/",)
PREFIX = {"photo": "img", "render": "img", "crop": "img", "sheet": "img", "program": "prg", "glb": "glb", "log": "log", "report": "doc",
          "receipt": "doc", "evidence": "doc", "mask": "dat", "blend": "dat", "parts": "dat", "bundle": "dat", "other": "art"}
MEDIA = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".json": "application/json",
         ".py": "text/x-python", ".glb": "model/gltf-binary", ".txt": "text/plain", ".md": "text/markdown", ".npz": "application/octet-stream",
         ".blend": "application/octet-stream", ".log": "text/plain", ".tar": "application/x-tar", ".jsonl": "application/x-ndjson"}
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
FILE_ATTRIBUTE_REPARSE_POINT = 0x400


class ArtifactError(StateError):
    pass


class AccessDenied(ArtifactError):
    pass


def is_reparse_point(path: Path) -> bool:
    """A symlink, junction or other reparse point (checked without following it)."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(st.st_mode):
        return True
    attrs = getattr(st, "st_file_attributes", 0)
    return bool(attrs & FILE_ATTRIBUTE_REPARSE_POINT)


def regular_file_or_raise(path: Path, *, max_bytes: int = MAX_ARTIFACT_BYTES) -> int:
    """Refuse links, reparse points, devices, directories and oversized files; returns the size."""
    if is_reparse_point(path):
        raise ArtifactError(f"{path} is a link or reparse point")
    st = os.lstat(path)
    if not stat.S_ISREG(st.st_mode):
        raise ArtifactError(f"{path} is not a regular file")
    if st.st_size > max_bytes:
        raise ArtifactError(f"{path} has {st.st_size} bytes, above the {max_bytes} limit")
    return st.st_size


def contained(child: Path, root: Path) -> bool:
    """True when ``child`` resolves inside ``root`` and no component below root is a reparse point.

    Both spellings are walked: the resolved path (a link whose target lies outside root fails the prefix test) and
    the path as given (``resolve`` follows a junction whose target lies inside root, so the junction component itself
    is only visible on the literal path)."""
    child, root = Path(child), Path(root)
    try:
        c = child.resolve(strict=False)
        r = root.resolve(strict=False)
    except OSError:
        return False
    if not c.is_relative_to(r):
        return False
    for base, path in ((r, c), (root, child)):
        try:
            parts = path.relative_to(base).parts
        except ValueError:
            continue        # the literal spelling is not below the literal root (relative or differently spelled): the resolved walk governs
        probe = base
        for part in parts:
            probe = probe / part
            if is_reparse_point(probe):
                return False
    return True


def atomic_write(path: Path, data: bytes) -> None:
    """Write to a temporary sibling, flush, fsync, then promote; the destination never exists half-written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    with tmp.open("xb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    try:
        os.replace(tmp, path)
    except FileExistsError:
        tmp.unlink(missing_ok=True)
        raise


def media_type_for(suffix: str) -> str:
    return MEDIA.get(suffix.lower(), "application/octet-stream")


def image_dimensions(data: bytes) -> tuple[int, int] | None:
    try:
        with Image.open(io.BytesIO(data)) as im:
            return int(im.width), int(im.height)
    except Exception:  # noqa: BLE001 - not an image
        return None


class ArtifactStore:
    def __init__(self, store: Store):
        self.store = store
        self.root = store.job_dir

    # ------------------------------------------------------------------ writing
    def add_bytes(self, data: bytes, *, kind: str, role: str, suffix: str, label: str = "", revision_id: str | None = None,
                  parent_id: str | None = None, recipe: dict | None = None, synthetic: bool = False) -> dict:
        if role not in ROLES:
            raise ArtifactError(f"unknown artifact role {role!r}")
        if kind not in PREFIX:
            raise ArtifactError(f"unknown artifact kind {kind!r}")
        if len(data) > MAX_ARTIFACT_BYTES:
            raise ArtifactError(f"artifact of {len(data)} bytes exceeds {MAX_ARTIFACT_BYTES}")
        if synthetic and role != SYNTHETIC:
            raise ArtifactError("synthetic output must live in the synthetic area")
        if parent_id is not None and self.store.artifact(parent_id) is None:
            raise ArtifactError(f"unknown parent artifact {parent_id}")
        digest = sha256_bytes(data)
        recipe = dict(recipe or {})
        dims = image_dimensions(data) if suffix.lower() in (".png", ".jpg", ".jpeg", ".webp") else None
        if dims:
            recipe.setdefault("width", dims[0])
            recipe.setdefault("height", dims[1])
        # the id is only known inside the transaction, so the file is written under a provisional name first and
        # the catalogue row points at the final name; the rename happens before the row commits
        with self.store.tx():
            seq = self.store._next_seq("artifacts")
            aid = f"{PREFIX[kind]}{seq:04d}"
            rel = Path(AREA[role]) / kind / f"{aid}__{digest[:12]}{suffix.lower()}"
            dest = self.root / rel
            if dest.exists():
                raise ArtifactError(f"{rel} exists; artifacts are never overwritten")
            atomic_write(dest, data)
            row = self.store.insert_artifact(kind=kind, role=role, rel_path=rel.as_posix(), sha256=digest, media_type=media_type_for(suffix),
                                             nbytes=len(data), revision_id=revision_id, parent_id=parent_id, recipe=recipe, label=label,
                                             synthetic=synthetic, prefix=PREFIX[kind])
            if row["id"] != aid:
                raise ArtifactError("artifact id sequence diverged")
        return row

    def add_file(self, path: Path, **kw) -> dict:
        path = Path(path)
        regular_file_or_raise(path)
        return self.add_bytes(path.read_bytes(), suffix=kw.pop("suffix", path.suffix), **kw)

    # ------------------------------------------------------------------ reading
    def path_of(self, artifact: dict | str) -> Path:
        row = self.store.artifact(artifact) if isinstance(artifact, str) else artifact
        if row is None:
            raise ArtifactError(f"unknown artifact {artifact}")
        p = self.root / row["rel_path"]
        if not contained(p, self.root):
            raise ArtifactError(f"artifact path {row['rel_path']} escapes the job folder")
        return p

    def read(self, artifact_id: str, *, allow_roles: tuple[str, ...]) -> tuple[dict, bytes]:
        """The verified bytes of an artifact whose role is allowed to the caller; AccessDenied otherwise."""
        row = self.store.artifact(artifact_id)
        if row is None:
            raise ArtifactError(f"unknown artifact {artifact_id}")
        if row["role"] not in allow_roles:
            raise AccessDenied(f"artifact {artifact_id} ({row['role']}) is not readable by this caller")
        p = self.path_of(row)
        regular_file_or_raise(p)
        data = p.read_bytes()
        if sha256_bytes(data) != row["sha256"]:
            raise ArtifactError(f"artifact {artifact_id} bytes changed on disk")
        return row, data

    def verify_all(self) -> dict:
        """Every catalogued file present with matching bytes; files on disk without a row are reported, never deleted."""
        problems, seen = [], set()
        for row in self.store.artifacts():
            p = self.root / row["rel_path"]
            seen.add(p.resolve())
            if not p.is_file():
                problems.append({"artifact": row["id"], "problem": "missing", "path": row["rel_path"]})
                continue
            if sha256_bytes(p.read_bytes()) != row["sha256"]:
                problems.append({"artifact": row["id"], "problem": "sha256_mismatch", "path": row["rel_path"]})
        orphans = []
        for area in AREA.values():
            base = self.root / area
            if not base.is_dir():
                continue
            for p in base.rglob("*"):
                if p.is_file() and p.resolve() not in seen:
                    rel = p.relative_to(self.root).as_posix()
                    if rel.startswith(UNCATALOGUED_PREFIXES):
                        continue
                    orphans.append(rel)
        return {"problems": problems, "orphans": sorted(orphans), "ok": not problems}

    def author_visible_ids(self) -> list[str]:
        return [a["id"] for a in self.store.artifacts(role=AUTHOR_VISIBLE)]
