"""Copy the historical camera fixture with its three explicit exported Prada front names.

Only model.front_parts changes. Camera values, masks, metrics and source GLB bytes
stay intact; production name matching remains exact.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil

from glasses3d.g3d.printuv import glbmesh as GM


REPO = Path(__file__).resolve().parents[3]
CAMERAS = REPO / "glasses3d/data/wp3b/prada/cameras.json"
CAMERAS_SHA = "a747e56473d8e1a1dcbebc5f70e8ec1af81f7627226f2d5435a6e4e459065faf"
MODEL_SHA = "975ebe344266067d686aff4fe7c4aa138a72a939d62861df3e28cda4f4fba7fe"
OLD_FRONT_PARTS = ("Front | continuous milled acetate", "Lens left | closed 49 x 36 mm",
                   "Lens right | closed 49 x 36 mm")
FRONT_PARTS = ("Front | continuous milled acetate.001", "Lens left | closed 49 x 36 mm.001",
              "Lens right | closed 49 x 36 mm.001")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def prepare(destination):
    root = Path(destination)
    root.mkdir(parents=True, exist_ok=False)
    if sha(CAMERAS) != CAMERAS_SHA:
        raise ValueError("historical Prada camera source changed")
    original = json.loads(CAMERAS.read_bytes())
    model = REPO / original["model"]["path"]
    if original["model"]["sha256"] != MODEL_SHA or sha(model) != MODEL_SHA:
        raise ValueError("historical Prada fitted model changed")
    doc, _ = GM.chunks(model.read_bytes())
    names = [node.get("name") for node in doc["nodes"] if "mesh" in node]
    if tuple(original["model"]["front_parts"]) != OLD_FRONT_PARTS or any(names.count(n) != 1 for n in FRONT_PARTS):
        raise ValueError("historical Prada front identity mapping is not one to one")
    adapted = deepcopy(original)
    adapted["model"]["front_parts"] = list(FRONT_PARTS)
    camera_path = root / "cameras.json"
    camera_path.write_text(json.dumps(adapted, indent=1), encoding="utf-8")
    sources = {str(CAMERAS): CAMERAS_SHA, str(model): MODEL_SHA}
    for overlay in CAMERAS.parent.glob("overlay_*.png"):
        sources[str(overlay)] = sha(overlay)
        shutil.copyfile(overlay, root / overlay.name)
    for rec in original["images"].values():
        if rec.get("matte_mode") == "supplied":
            source = CAMERAS.parent / rec["matte_file"]
            target = root / rec["matte_file"]
            target.parent.mkdir(parents=True, exist_ok=True)
            sources[str(source)] = sha(source)
            shutil.copyfile(source, target)
    reversed_doc = deepcopy(adapted)
    reversed_doc["model"]["front_parts"] = list(OLD_FRONT_PARTS)
    assert reversed_doc == original
    assert all(sha(path) == digest for path, digest in sources.items())
    receipt = {"schema": "g3d.test_fixture_migration/1", "sources": sources,
               "cameras": str(camera_path), "cameras_sha256": sha(camera_path),
               "changed_fields": ["model.front_parts"], "old": list(OLD_FRONT_PARTS), "new": list(FRONT_PARTS)}
    (root / "migration.json").write_text(json.dumps(receipt, indent=1), encoding="utf-8")
    return {"cameras": camera_path, "receipt": receipt}
