"""Build the raw-render manifest from pinned comparison inputs and downloaded GLBs."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", required=True, type=Path)
    parser.add_argument("--runs", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--orientations", type=Path, help="JSON mapping case ID to [x,y,z] degrees or {rotation_degrees, ...}")
    parser.add_argument("--require-all", action="store_true")
    parser.add_argument("--modes", default="raw,normals")
    parser.add_argument("--backgrounds", default="light,checker")
    args = parser.parse_args()
    inputs_path = args.inputs.resolve()
    inputs = json.loads(inputs_path.read_text(encoding="utf-8-sig"))
    runs = (args.runs or inputs_path.parent / "runs").resolve()
    orientations = json.loads(args.orientations.read_text(encoding="utf-8-sig")) if args.orientations else {}
    cases, products, missing = [], {}, []
    for product, source in inputs["products"].items():
        products[product] = {"photos": {view: str(Path(photo["path"]).resolve()) for view, photo in source["photos"].items()}}
        baseline = source["baseline"]
        model = Path(baseline["path"])
        if digest(model) != baseline["sha256"]:
            raise ValueError(f"Pinned baseline hash mismatch for {product}")
        cases.append({"id": f"{product}-meshy", "product": product, "provider": "meshy-cached", "path": str(model.resolve()),
                      "model_sha256": baseline["sha256"], "source": "cached baseline"})
        for provider in ("rodin", "tripo", "trellis"):
            identifier = f"{product}-{provider}"
            model = runs / identifier / "artifacts/model.glb"
            if not model.is_file():
                missing.append(identifier)
                continue
            cases.append({"id": identifier, "product": product, "provider": provider, "path": str(model),
                          "model_sha256": digest(model), "source": "downloaded raw provider output"})
    if args.require_all and missing:
        raise SystemExit("Still missing downloaded models: " + ", ".join(missing))
    for case in cases:
        orientation = orientations.get(case["id"], [0, 0, 0])
        if isinstance(orientation, dict):
            case.update({key: value for key, value in orientation.items() if key in ("rotation_degrees", "orientation_basis", "width_mm")})
        else:
            case["rotation_degrees"] = orientation
    manifest = {"schema_version": 1, "inputs_path": str(inputs_path), "inputs_sha256": digest(inputs_path),
                "products": products, "cases": cases, "missing_downloads": missing,
                "modes": args.modes.split(","), "backgrounds": args.backgrounds.split(","),
                "views": ["front", "back", "left", "right", "angled"],
                "orientation_status": "Per-case orientation is declared and must be visually checked against photographs before judging geometry."}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"manifest": str(args.output.resolve()), "cases": [case["id"] for case in cases], "missing": missing}, indent=2))


if __name__ == "__main__":
    main()
