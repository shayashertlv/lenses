import pytest

from glasses3d.lens_lab.ceiling import design, material_code, texture_receipts, verify_export, worker_capacity


def test_design_is_systematic_and_all_declared_dimensions_vary():
    first, second = design(), design()
    first.pop("created")
    second.pop("created")
    assert first == second
    rows = first["candidates"]
    assert len(rows) == 1 + 216 + 128 + 144
    assert len({row["id"] for row in rows}) == len(rows)
    for key in ("metallic", "roughness", "transmission", "ior", "specular_factor", "film_factor",
                "film_ior", "thickness_min_nm", "thickness_max_nm"):
        assert len({row[key] for row in rows}) > 4
    assert {row["texture_profile"] for row in rows} == {None, "centre_peak", "edges_peak"}
    assert {(row["thickness_min_nm"], row["thickness_max_nm"]) for row in rows if row["texture_profile"]} == {
        (50., 350.), (100., 550.), (250., 750.), (450., 950.)}


def test_memory_capacity_leaves_headroom_and_caps_at_fourteen():
    assert worker_capacity(100 * 2**30, 32) == 14
    assert worker_capacity(6 * 2**30, 16) == 3
    assert worker_capacity(1 * 2**30, 16) == 0
    assert worker_capacity(3.2 * 2**30, 16) == 0
    assert worker_capacity(100 * 2**30, 2) == 2


@pytest.mark.parametrize("destination", ["outside", "traversal", "root"])
def test_run_refuses_nonprivate_output_before_heavy_gate(tmp_path, monkeypatch, destination):
    from glasses3d.lens_lab import ceiling, harness, score
    lab = tmp_path / "lab"
    lab.mkdir()
    monkeypatch.setattr(score, "DATA", lab)
    monkeypatch.setattr(harness, "ensure_heavy_allowed", lambda: pytest.fail("outside output reached heavy gate"))
    output = {"outside": tmp_path / "outside", "traversal": lab / ".." / "outside", "root": lab}[destination]
    with pytest.raises(ValueError, match="private subdirectory"):
        ceiling.run(output)
    assert list(lab.iterdir()) == [] and not (tmp_path / "outside").exists()


@pytest.mark.parametrize(("free_gib", "workers"), [(1, None), (1, 1), (6, 0), (6, 4)])
def test_run_refuses_unavailable_capacity_before_output_or_pool(tmp_path, monkeypatch, free_gib, workers):
    from glasses3d.g3d_host import resources
    from glasses3d.lens_lab import ceiling, harness, score
    monkeypatch.setattr(score, "DATA", tmp_path)
    monkeypatch.setattr(harness, "ensure_heavy_allowed", lambda: None)
    monkeypatch.setattr(resources, "free_commit_bytes", lambda: free_gib * 2**30)
    monkeypatch.setattr(score, "load_basis", lambda *args: pytest.fail("unavailable capacity reached basis"))
    monkeypatch.setattr(ceiling, "ProcessPoolExecutor", lambda **kwargs: pytest.fail("unavailable capacity started pool"))
    output = tmp_path / "search"
    with pytest.raises(ValueError, match="current free commit"):
        ceiling.run(output, workers=workers)
    assert not output.exists()


def test_export_verification_rejects_dropped_texture():
    candidate = design(sobol_power=1)["candidates"][-1]
    material = {"name": "lens", "pbrMetallicRoughness": {
        "baseColorFactor": [*candidate["base_rgb"], 1], "metallicFactor": candidate["metallic"],
        "roughnessFactor": candidate["roughness"]}, "extensions": {
        "KHR_materials_transmission": {"transmissionFactor": candidate["transmission"]},
        "KHR_materials_ior": {"ior": candidate["ior"]},
        "KHR_materials_iridescence": {"iridescenceFactor": candidate["film_factor"],
            "iridescenceIor": candidate["film_ior"], "iridescenceThicknessMinimum": candidate["thickness_min_nm"],
            "iridescenceThicknessMaximum": candidate["thickness_max_nm"]}}}
    with pytest.raises(ValueError, match="texture"):
        verify_export(candidate, [material])
    material["extensions"]["KHR_materials_iridescence"]["iridescenceThicknessTexture"] = {"index": 0}
    assert verify_export(candidate, [material])
    material["pbrMetallicRoughness"]["baseColorFactor"] = [.1, .1, .1, 1.]
    with pytest.raises(ValueError, match="base"):
        verify_export(candidate, [material])


def test_diagnostic_material_code_uses_uv_material_graph_without_mesh_mutations():
    from glasses3d.g3d.safe_mode import validate
    source = material_code(design(sobol_power=1)["candidates"][-1], ["shield"])
    compile(source, "<lab diagnostic>", "exec")
    assert validate(source) == []
    assert "g3d.lens_coating" in source
    assert "Iridescence Thickness Minimum" in source
    assert "ShaderNodeSeparateColor" in source
    assert "bpy.data.meshes" not in source
    assert "uv_layers" not in source


def test_texture_proof_reads_embedded_green_and_actual_uv(tmp_path):
    import io
    import json
    import struct
    from PIL import Image

    out = io.BytesIO()
    image = Image.new("RGB", (3, 1))
    image.putdata([(200, 0, 200), (0, 255, 0), (200, 0, 200)])
    image.save(out, format="PNG")
    binary = out.getvalue()
    doc = {"materials": [{"name": "lens", "extensions": {"KHR_materials_iridescence":
           {"iridescenceThicknessTexture": {"index": 0}}}}],
           "textures": [{"source": 0}], "images": [{"bufferView": 0}],
           "bufferViews": [{"byteOffset": 0, "byteLength": len(binary)}],
           "meshes": [{"primitives": [{"material": 0, "attributes": {"TEXCOORD_0": 1}}]}]}
    def write():
        encoded = json.dumps(doc).encode()
        encoded += b" " * (-len(encoded) % 4)
        payload = binary + b"\0" * (-len(binary) % 4)
        path = tmp_path / "texture.glb"
        path.write_bytes(struct.pack("<III", 0x46546c67, 2, 28 + len(encoded) + len(payload)) +
                         struct.pack("<II", len(encoded), 0x4e4f534a) + encoded +
                         struct.pack("<II", len(payload), 0x004e4942) + payload)
        return path
    assert texture_receipts(write(), ["lens"], "centre_peak")[0]["green_max"] == 255
    with pytest.raises(ValueError, match="profile"):
        texture_receipts(write(), ["lens"], "edges_peak")
    doc["meshes"][0]["primitives"][0]["attributes"] = {}
    with pytest.raises(ValueError, match="UV"):
        texture_receipts(write(), ["lens"], "centre_peak")
