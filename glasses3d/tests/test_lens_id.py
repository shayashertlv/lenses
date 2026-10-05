import json
from pathlib import Path

import pytest

from glasses3d.g3d_host import lens_id

CASES = json.loads((Path(__file__).parent / "fixtures" / "lens_id_cases.json").read_text(encoding="utf-8"))["cases"]


def ref(p):
    return lens_id.PrimitiveRef(key=p["key"], node_name=p.get("node_name"), node_extras=p.get("node_extras"),
                                mesh_extras=p.get("mesh_extras"), material_name=p.get("material_name"),
                                material_extras=p.get("material_extras"), material=p.get("material"))


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_shared_vectors(case):
    decisions, _ = lens_id.classify([ref(p) for p in case["prims"]])
    got = {k: [d.is_lens, d.rule] for k, d in decisions.items()}
    assert got == case["expect"]


def test_tokens_split_camel_case_digits_and_separators():
    assert lens_id.tokens("LensRight.001") == ["lens", "right"]
    assert lens_id.tokens("Lens | light brown") == ["lens", "light", "brown"]
    assert lens_id.tokens(None) == []


def test_tokens_split_acronym_case():
    assert lens_id.tokens("LENSRight") == ["lens", "right"]
    assert lens_id.tokens("GLASSLeft") == ["glass", "left"]
    assert lens_id.tokens("XMLLens2Frame") == ["xml", "lens", "frame"]
    assert lens_id.tokens("SUNGLASSES") == ["sunglasses"]


def test_part_role_is_trimmed_and_case_insensitive():
    for role in ("lens", "Lens", "LENS", " lens ", "\tLens\n"):
        d, _ = lens_id.classify([ref({"key": "k", "node_name": "x", "mesh_extras": {"partRole": role},
                                      "material_name": "m"})])
        assert d["k"].is_lens and d["k"].rule == 1, role
        assert d["k"].signal == "mesh.extras.partRole=" + role.strip()
    d, _ = lens_id.classify([ref({"key": "k", "node_name": "x", "mesh_extras": {"partRole": "lenses"},
                                  "material_name": "m"})])
    assert not d["k"].is_lens


def test_last_resort_and_conflicts_are_warned():
    _, warn = lens_id.classify([ref({"key": "t", "node_name": "O", "material_name": "M",
                                     "material": {"extensions": {"KHR_materials_transmission": {"transmissionFactor": 1}}}})])
    assert any("last resort" in w for w in warn)
    _, warn = lens_id.classify([ref({"key": "m", "node_name": "S", "node_extras": {"partRole": "frame"},
                                     "material_name": "S", "material_extras": {"partRole": "lens"}})])
    assert any("disagrees" in w for w in warn)


def test_signal_names_the_deciding_source():
    d, _ = lens_id.classify([ref({"key": "a", "node_name": "Lens_L", "mesh_extras": {"partRole": "lens"},
                                  "material_name": "x"})])
    assert d["a"].signal == "mesh.extras.partRole=lens"
