"""Historical camera reproduction data and helpers; never imported by runtime code."""
from dataclasses import dataclass, field
from pathlib import Path
from datetime import datetime, timezone
import json
import shutil
import numpy as np
from glasses3d.g3d_host import cameras, fitmesh
from glasses3d.g3d_host.fit_views import fit_views, sha256_file
G3D = Path(__file__).resolve().parents[2]
REFERENCE = G3D/'data/reference'
OUT = G3D/'data/out/wp3b'
MANIFEST = 'manifest.json'
MODEL = 'model.glb'
PROTOTYPE_SKIP = ("inner_brand_print", "TOM_FORD", "medallion")
PROTOTYPE_FRONT_PARTS = ("Brow_Left", "Brow_Right", "Eyewire_Left", "Eyewire_Right", "Lens_Left", "Lens_Right",
                         "Saddle_bridge")
@dataclass(frozen=True)
class Product:
    key: str
    name: str
    job: str                                          # studio job id the photos come from (PLAN §5)
    model_source: str                                 # where model.glb came from (provenance only)
    views: dict[str, str]                             # photo -> PLAN §5 label (job.json labels are ignored, §0.3)
    skip: tuple[str, ...] = ()                        # fitmesh skip substrings (thin surface decals)
    front_parts: tuple[str, ...] | None = None        # pivot parts (None: the geometric front piece)
    n_refine: dict[str, int] = field(default_factory=dict)   # per-photo override of cameras.N_COARSE_POWELL


PRADA_SKIP = ("hallmark", "etch", "ribbon")           # gold temple hallmarks/ribbon and the lens etch: surface decals
PRADA_FRONT_PARTS = ("Front | continuous milled acetate.001", "Lens left | closed 49 x 36 mm.001",
                     "Lens right | closed 49 x 36 mm.001")
# make_uv_test.py fitted FT1119 02/03/04 with 2 starts; keeping 2 keeps the reproduced prototype cameras for 02/03
# (tests/test_cameras_repro.py) as the cameras WP5 projects with; 04 also gets the top prior's low band, which lands in
# the prototype's basin (within 0.06-0.21 deg, tested) but not bit-identically
FT1119_N_REFINE = {"reference-02.png": 2, "reference-03.png": 2, "reference-04.png": 2}

PRODUCTS: dict[str, Product] = {
    "ft1119": Product(
        key="ft1119", name="Tom Ford FT1119-D 52A", job="d4b716a6afc445f89cbed8beddad4c37",
        model_source="studio job 3b8a1c7ae8114868b49b298b681f1b29 original/model.glb (quality-001, = prototype q001.glb)",
        views={"reference-01.png": "angled", "reference-02.png": "rear", "reference-03.png": "side",
               "reference-04.png": "top", "reference-05.png": "front", "reference-06.png": "elevated side"},
        skip=PROTOTYPE_SKIP,
        front_parts=("Brow_Left.001", "Brow_Right.001", "Eyewire_Left.001", "Eyewire_Right.001",
                     "Lens_Left.001", "Lens_Right.001", "Saddle_bridge.001"),
        n_refine=FT1119_N_REFINE),
    "prada": Product(
        key="prada", name="Prada PRS 17WS 21D5J2 49M", job="8c82d986e6c14d26b4b38f3807712ea0",
        model_source="studio job 8c82d986e6c14d26b4b38f3807712ea0 agent/ar/prada-pr17ws-delivery-3e3ac97b/model.glb",
        views={"reference-01.webp": "side", "reference-02.webp": "angled", "reference-03.png": "top",
               "reference-04.png": "front", "reference-05.png": "side", "reference-06.png": "rear"},
        skip=PRADA_SKIP, front_parts=PRADA_FRONT_PARTS),
}


# ----------------------------------------------------------------------------- reference data
class ReferenceDataMissing(FileNotFoundError):
    """The product's reference data was never imported (no manifest)."""


class ReferenceDataError(RuntimeError):
    """The manifest exists but a file is missing or differs from it."""


def reference_dir(product: str, root: Path | None = None) -> Path:
    return (REFERENCE if root is None else Path(root)) / product


def manifest_path(product: str, root: Path | None = None) -> Path:
    return reference_dir(product, root) / MANIFEST


def reference_files(product: str, root: Path | None = None) -> dict[str, Path]:
    """Every manifest file of the product (photos and ``model.glb``), each checked against its sha256 and size.
    Raises ``ReferenceDataMissing`` without a manifest and ``ReferenceDataError`` for a missing or changed file."""
    mp = manifest_path(product, root)
    if not mp.exists():
        raise ReferenceDataMissing(f"{mp} not found: import these historical inputs with tests.fixtures.camera_products.import_references")
    man = json.loads(mp.read_text(encoding="utf-8"))
    out, bad = {}, []
    for name, rec in man["files"].items():
        p = mp.parent / name
        if not p.exists():
            bad.append(f"{name}: missing")
        elif p.stat().st_size != rec["bytes"] or sha256_file(p) != rec["sha256"]:
            bad.append(f"{name}: differs from the manifest (sha256 {rec['sha256'][:12]}...)")
        out[name] = p
    if bad:
        raise ReferenceDataError(f"{mp.parent}: " + "; ".join(bad))
    if product in PRODUCTS:
        want = set(PRODUCTS[product].views) | {MODEL}
        if set(out) != want:
            raise ReferenceDataError(f"{mp}: files {sorted(out)} != the product table's {sorted(want)}")
    return out


def import_references(product: str, job_dir: Path, model: Path, root: Path | None = None) -> dict:
    """Copy the product's photos (from ``job_dir/images``, each checked against the job's own sha256) and the model
    into ``data/reference/<product>/`` and write the manifest. Never overwrites a file with different content."""
    prod = PRODUCTS[product]
    job_dir, model = Path(job_dir), Path(model)
    job = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    if job.get("id") not in (None, prod.job):
        raise ValueError(f"{job_dir}: job id {job.get('id')} is not {prod.job}")
    job_sha = {im["name"]: im.get("sha256") for im in job.get("images", [])}
    dst = reference_dir(product, root)
    dst.mkdir(parents=True, exist_ok=True)
    files = {}
    for name, label in prod.views.items():
        src = job_dir / "images" / name
        sha = sha256_file(src)
        if job_sha.get(name) != sha:
            raise ValueError(f"{src}: sha256 {sha} != job.json's {job_sha.get(name)}")
        files[name] = _copy_checked(src, dst / name, sha) | {"label": label, "source": f"job {prod.job} images/{name}"}
    msha = sha256_file(model)
    files[MODEL] = _copy_checked(model, dst / MODEL, msha) | {"source": prod.model_source}
    man = {"product": product, "name": prod.name, "job": prod.job,
           "imported_utc": _utc(),
           "note": "labels are PLAN.md §5 (job.json view labels ignored, PLAN §0.3); every photo is authoritative",
           "files": files}
    (dst / MANIFEST).write_text(json.dumps(man, indent=1), encoding="utf-8")
    return man


def _copy_checked(src: Path, dst: Path, sha: str) -> dict:
    if dst.exists():
        if sha256_file(dst) != sha:
            raise ValueError(f"{dst} exists with different content; refusing to overwrite")
    else:
        shutil.copyfile(src, dst)
        if sha256_file(dst) != sha:
            raise ValueError(f"{dst}: copy differs from {src}")
    return {"sha256": sha, "bytes": dst.stat().st_size}


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_mesh(product: str, files: dict[str, Path]) -> tuple[fitmesh.FitMesh, np.ndarray]:
    """The product's fit mesh (table skip list) and its normalized front pivot (table front parts)."""
    prod = PRODUCTS[product]
    mesh = fitmesh.load_fit_mesh(files[MODEL], skip=prod.skip)
    return mesh, mesh.front_pivot(prod.front_parts)


# ----------------------------------------------------------------------------- one product of the table
def fit_product(product: str, only: list[str] | None = None, candidates: dict[str, tuple[str, ...]] | None = None,
                workers: int | None = None, out_dir: Path | None = None, root: Path | None = None,
                log=print) -> dict:
    """``fit_views`` on a product's sha256-checked reference data with the product table's mesh settings."""
    prod = PRODUCTS[product]
    files = reference_files(product, root)            # fail fast on missing/changed data
    unknown = sorted(set(only or ()) - set(prod.views)) + sorted(set(candidates or {}) - set(prod.views))
    if unknown:
        raise ValueError(f"{product}: unknown references {unknown}; known: {list(prod.views)}")
    names = [n for n in prod.views if not only or n in only]
    res = fit_views(files[MODEL], {n: files[n] for n in names}, {n: prod.views[n] for n in names},
                    OUT / product if out_dir is None else out_dir, skip=prod.skip, front_parts=prod.front_parts,
                    candidates={n: c for n, c in (candidates or {}).items() if n in names},
                    n_refine={n: k for n, k in prod.n_refine.items() if n in names}, workers=workers, log=log)
    return {"product": product, "name": prod.name, **res}


def main(argv=None):
    """Historical import/fit CLI moved intact out of the product-independent runtime."""
    import argparse
    from glasses3d.g3d_host.fit_views import _parse_candidates
    from glasses3d.g3d_host.compare import write_json
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest='command', required=True)
    imp = sub.add_parser('import')
    imp.add_argument('product', choices=sorted(PRODUCTS))
    imp.add_argument('--job-dir', required=True, type=Path)
    imp.add_argument('--model', required=True, type=Path)
    fit = sub.add_parser('fit')
    fit.add_argument('product', choices=sorted(PRODUCTS))
    fit.add_argument('--only', nargs='*')
    fit.add_argument('--candidates', nargs='*')
    fit.add_argument('--workers', type=int)
    fit.add_argument('--out', type=Path)
    fit.add_argument('--out-dir', type=Path)
    args = ap.parse_args(argv)
    if args.command == 'import':
        print(json.dumps(import_references(args.product, args.job_dir, args.model), indent=1))
    else:
        result = fit_product(args.product, only=args.only, candidates=_parse_candidates(args.candidates),
                             workers=args.workers, out_dir=args.out_dir)
        if args.out:
            write_json(args.out, result)
        print(f"{len(result['images'])} references in {result['seconds']:.0f} s")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
