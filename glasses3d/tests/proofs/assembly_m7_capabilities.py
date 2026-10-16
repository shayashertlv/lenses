"""Record complete M7/M8 request mappings without a model, estimate or edit run.

The declared mapping proves availability, not the correctness of an interpretation
or a geometric result. Real labelled previews and owner confirmation remain needed.
Run from the repository parent with ``python -m glasses3d.tests.proofs.assembly_m7_capabilities OUT``.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import sys

from glasses3d.agent import edit_capabilities as C


def mapping(lines, operations):
    request = "\n".join(lines)
    offset, items = 0, []
    for number, (line, kinds) in enumerate(zip(lines, operations, strict=True), 1):
        items.append({"id": str(number), "label": line, "start": offset,
                      "end": offset + len(line), "text": line, "operation_kinds": kinds})
        offset += len(line) + 1
    return {"schema": C.MAPPING_SCHEMA, "request_text": request, "items": items}


def run(out):
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    assembly = (Path(__file__).resolve().parents[2] / "plan/ASSEMBLY.md").read_text(encoding="utf-8")
    m7 = assembly.split("### M7.", 1)[1].split("### M8.", 1)[0]
    words = re.search(r'verbatim: "(.*?)"', m7, flags=re.DOTALL).group(1)
    words = " ".join(words.split())
    sentences = re.findall(r"[^.]+\.", words)
    assert len(sentences) == 4 and "".join(sentences) == words
    # Keep the exact single-space owner sentence separators rather than normalizing
    # the request into a different string for the checker.
    # The front line includes an authored geometric ramp as well as split shading.
    # Shading alone cannot reverse that measured geometry cause.
    record = mapping([line.strip() for line in sentences], [
        ["flatten_face"], ["flatten_face"], ["taper_region", "repair_shading"],
        ["refine_curved_surface"],
    ])
    record["request_text"] = words
    for item in record["items"]:
        assert words[item["start"]:item["end"]] == item["text"]
    m8 = mapping([
        "Colour and material adjust.",
        "Round or sharpen edges, with mesh changes allowed only inside the selection.",
        "Thicker or thinner.",
        "Scale a part or region about an anchor.",
        "Bend with falloff.",
        "Move, resize or rotate a marking on its surface.",
        "Mesh-health helpers for Astra in the main run: clean degenerate triangles and duplicate vertices, make normals consistent, symmetrize about X=0, and controlled decimation that keeps silhouette, sharp edges and markings.",
    ], [["material_adjust"], ["edge_shape"], ["thickness"], ["scale_region"],
        ["bend_region"], ["transform_marking"],
        ["mesh_cleanup", "consistent_normals", "symmetrize", "controlled_decimate"]])
    for name, declaration in (("m7", record), ("m8", m8)):
        report = C.check(declaration["request_text"], declaration)
        assert report["status"] == "ready", report
        C.require_ready(report, declaration["request_text"])
        request_file = out / f"{name}-request.txt"
        request_file.write_text(declaration["request_text"], encoding="utf-8", newline="")
        assert request_file.read_bytes().decode("utf-8") == declaration["request_text"]
        C.write_report(out / f"{name}-mapping.json", declaration)
        C.write_report(out / f"{name}-coverage.json", report)
    print(json.dumps({"path": str(out), "m7_items": 4, "m8_items": 7,
                      "api_calls": 0, "cost_usd": 0, "owner_confirmation": False}))


if __name__ == "__main__":
    run(sys.argv[1])
