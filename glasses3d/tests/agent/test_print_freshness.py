"""Actual GLB geometry, UV and image binding checks against retained miniature bakes."""
import hashlib
import io
import json
from pathlib import Path
import struct

import numpy as np
from PIL import Image
import pytest

from glasses3d.agent.print_freshness import ATTACHMENT_SCHEMA, check_current_print
from glasses3d.g3d import printuv as PU
from glasses3d.g3d.printuv import glbmesh as GM


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    Path(path).write_text(json.dumps(value), encoding="utf-8")


def edit_glb_json(path, edit):
    doc, binary = GM.chunks(path.read_bytes())
    edit(doc)
    body = json.dumps(doc).encode()
    body += b" " * (-len(body) % 4)
    payload = struct.pack("<II", len(body), 0x4E4F534A) + body
    payload += struct.pack("<II", len(binary), 0x004E4942) + binary
    path.write_bytes(struct.pack("<4sII", b"glTF", 2, len(payload)+12) + payload)


def mesh(path, names=("A", "B"), *, paints=None, move=None, uv_shift=0., strip_marker=False,
         strip_metadata=False, changed_image=False, image_reencode=False, image_name=None, detached=False,
         missing_source=False):
    paints = paints or {}
    data = bytearray()
    doc = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": list(range(len(names)))}],
           "nodes": [], "meshes": [], "materials": [], "textures": [], "images": [],
           "accessors": [], "bufferViews": [], "buffers": []}

    def view(raw):
        data.extend(b"\0" * (-len(data) % 4))
        index = len(doc["bufferViews"])
        doc["bufferViews"].append({"buffer": 0, "byteOffset": len(data), "byteLength": len(raw)})
        data.extend(raw)
        return index

    def accessor(array, kind, dtype, component):
        array = np.asarray(array, dtype=dtype)
        index = len(doc["accessors"])
        doc["accessors"].append({"bufferView": view(array.tobytes()), "componentType": component,
                                 "count": len(array), "type": kind})
        return index

    for i, name in enumerate(names):
        positions = np.array([[i*.02, 0, 0], [i*.02+.01, 0, 0], [i*.02, .01, 0]], np.float32)
        if move == name:
            positions[0, 0] += .001
        attributes = {"POSITION": accessor(positions, "VEC3", "<f4", 5126)}
        primitive = {"attributes": attributes, "indices": accessor([0, 1, 2], "SCALAR", "<u2", 5123),
                     "material": i}
        material = {"name": "authored-" + name, "pbrMetallicRoughness": {"baseColorFactor": [.3, .4, .5, 1.]}}
        node = {"name": name, "mesh": i, "extras": {"partRole": "frame"}}
        if name in paints:
            bake = paints[name]
            man = bake["manifest"]
            row = bake["layout"].face_index(len(bake["F"]))[GM.part_faces(bake["part"], list(names), name)]
            uv = bake["layout"].uv01()[row].reshape(-1, 2) + uv_shift
            attributes["TEXCOORD_0"] = accessor(uv, "VEC2", "<f4", 5126)
            info = {"layer": "g3d_print", "texcoord": 0, "geometry_sha": man["geometry_sha"],
                    "charts_sha": man["charts_sha"], "atlas": [man["width"], man["height"]],
                    "tx_per_mm": man["tx_per_mm"]}
            if detached:
                attributes["TEXCOORD_1"] = attributes["TEXCOORD_0"]
                attributes["TEXCOORD_0"] = accessor([[0, 0], [1, 0], [0, 1]], "VEC2", "<f4", 5126)
                info["texcoord"] = 1
            if not strip_marker:
                info["attachment"] = {"schema": ATTACHMENT_SCHEMA,
                                       **{k: man[k] for k in ("print_sha256", "geometry_sha", "charts_sha")}}
            if not strip_metadata:
                node["extras"]["g3d_print_uv"] = json.dumps(info)
            raw = Path(man["print_png"]).read_bytes()
            if changed_image or image_reencode:
                image = Image.open(io.BytesIO(raw)).convert("RGB")
                if changed_image:
                    image.putpixel((0, 0), (255, 0, 255))
                buf = io.BytesIO()
                image.save(buf, format="PNG", compress_level=0)
                raw = buf.getvalue()
            index = len(doc["images"])
            doc["images"].append({"bufferView": view(raw), "mimeType": "image/png",
                                   "name": image_name or "g3d print " + man["print_sha256"][:12]})
            doc["textures"].append({} if missing_source else {"source": index})
            material["pbrMetallicRoughness"]["baseColorTexture"] = {"index": index, "texCoord": 0}
        doc["nodes"].append(node)
        doc["meshes"].append({"primitives": [primitive]})
        doc["materials"].append(material)
    data.extend(b"\0" * (-len(data) % 4))
    doc["buffers"] = [{"byteLength": len(data)}]
    body = json.dumps(doc).encode()
    body += b" " * (-len(body) % 4)
    payload = struct.pack("<II", len(body), 0x4E4F534A) + body + struct.pack("<II", len(data), 0x004E4942) + data
    path.write_bytes(struct.pack("<4sII", b"glTF", 2, len(payload)+12) + payload)
    return path


def bake(root, tag, parts, color=(80, 100, 120)):
    folder = root / "work/print" / tag
    folder.mkdir(parents=True)
    source = root / "source.glb"
    if not source.exists():
        mesh(source)
    V, F, part, names = GM.mesh_arrays(GM.read_glb(source))
    layout = PU.layout(V, F, part, names, parts, 2., mirror=False)
    png = folder / "print.png"
    Image.new("RGB", (layout.width, layout.height), color).save(png)
    manifest = {"version": 1, "parts": parts, "texture_parts": parts, "no_texture_parts": [],
                "glb": str(source), "glb_sha256": sha(source), "print_png": str(png), "print_sha256": sha(png),
                "layer": "g3d_print", "tx_per_mm": 2., "tx_requested": 2., "width": layout.width,
                "height": layout.height, "geometry_sha": GM.geometry_sha(V, F, part, names, parts),
                "charts_sha": GM.charts_sha(layout.charts, layout.width, layout.height)}
    path = folder / "print_manifest.json"
    write(path, manifest)
    app = {"status": "ok", "manifest_sha256": sha(path), "source_export": {"sha256": sha(source)},
           "texture_parts": parts, "production": {"print_sha256": sha(png)},
           "export": {"print_check": {"ok": False, "status": "pending"}}}
    write(folder / "application.json", app)
    return {"manifest": manifest, "folder": folder, "application": app, "layout": layout, "F": F, "part": part}


def test_plain_and_fresh_print_and_detached_model(tmp_path):
    plain = mesh(tmp_path / "current.glb")
    assert check_current_print(plain, tmp_path)["status"] == "not_required"
    a = bake(tmp_path, "first", ["A"])
    mesh(plain, paints={"A": a}, strip_marker=True)
    result = check_current_print(plain, tmp_path)
    assert result["ok"] and result["status"] == "verified", result
    assert result["glb_sha256"] == sha(plain) and len(result["attachments"]) == 1
    assert result["attachments"][0]["check"]["parts"]["A"]["uv_checked"]
    mesh(plain)
    assert check_current_print(plain, tmp_path)["status"] == "not_required"


@pytest.mark.parametrize("change", [{"move": "A"}, {"uv_shift": .05}, {"changed_image": True},
                                    {"move": "A", "strip_marker": True}, {"strip_metadata": True},
                                    {"strip_marker": True, "changed_image": True, "image_name": "edited"},
                                    {"strip_marker": True, "missing_source": True}])
def test_stale_geometry_uv_image_or_stripped_metadata_is_refused(tmp_path, change):
    a = bake(tmp_path, "first", ["A"])
    path = mesh(tmp_path / "current.glb", paints={"A": a}, **change)
    result = check_current_print(path, tmp_path)
    assert not result["ok"] and result["status"] == "refused", result


def test_marker_stripping_and_reencoded_png_cannot_skip_checks(tmp_path):
    a = bake(tmp_path, "first", ["A"])
    path = mesh(tmp_path / "current.glb", paints={"A": a}, strip_marker=True, image_reencode=True)
    result = check_current_print(path, tmp_path)
    assert result["ok"] and result["status"] == "verified", result
    mesh(path, paints={"A": a}, strip_marker=True, image_reencode=True, move="A")
    assert not check_current_print(path, tmp_path)["ok"]


@pytest.mark.parametrize("change", ["missing_application", "failed_application", "manifest", "png", "source"])
def test_missing_or_tampered_retained_evidence_refuses(tmp_path, change):
    a = bake(tmp_path, "first", ["A"])
    path = mesh(tmp_path / "current.glb", paints={"A": a})
    folder = a["folder"]
    if change == "missing_application":
        (folder / "application.json").unlink()
    elif change == "failed_application":
        write(folder / "application.json", {**a["application"], "status": "refused"})
    elif change == "manifest":
        write(folder / "print_manifest.json", {**a["manifest"], "label": "edited"})
    elif change == "png":
        (folder / "print.png").write_bytes(b"changed")
    else:
        mesh(tmp_path / "source.glb", move="A")
    assert not check_current_print(path, tmp_path)["ok"]


def test_restored_old_bake_and_complete_disjoint_groups(tmp_path):
    old = bake(tmp_path, "old", ["A"], (20, 40, 60))
    new = bake(tmp_path, "new", ["A"], (80, 100, 120))
    other = bake(tmp_path, "other", ["B"], (100, 120, 140))
    path = mesh(tmp_path / "current.glb", paints={"A": new, "B": other})
    assert len(check_current_print(path, tmp_path)["attachments"]) == 2
    mesh(path, paints={"A": old, "B": other})
    result = check_current_print(path, tmp_path)
    assert result["ok"] and len(result["attachments"]) == 2, result
    assert any(row["manifest"].endswith("old\\print_manifest.json") or
               row["manifest"].endswith("old/print_manifest.json") for row in result["attachments"])


def test_partial_old_group_detach_requires_complete_group_action(tmp_path):
    a = bake(tmp_path, "group", ["A", "B"])
    path = mesh(tmp_path / "current.glb", paints={"A": a})
    result = check_current_print(path, tmp_path)
    assert not result["ok"] and "complete old bake group" in result["reasons"][0]


def test_no_cached_application_verdict_is_trusted(tmp_path):
    a = bake(tmp_path, "first", ["A"])
    path = mesh(tmp_path / "current.glb", paints={"A": a})
    assert check_current_print(path, tmp_path)["ok"]
    write(a["folder"] / "application.json", {**a["application"], "export": {"print_check": {"ok": True}}})
    mesh(path, paints={"A": a}, move="A")
    assert not check_current_print(path, tmp_path)["ok"]


def test_copied_marker_and_image_cannot_claim_an_outside_part(tmp_path):
    a = bake(tmp_path, "first", ["A"])
    path = mesh(tmp_path / "current.glb", paints={"A": a, "B": a})
    result = check_current_print(path, tmp_path)
    assert not result["ok"] and any("B:" in r and "not an eligible part" in r for r in result["reasons"])


def test_detach_restoring_authored_texture_does_not_require_old_print(tmp_path):
    a = bake(tmp_path, "first", ["A"])
    path = mesh(tmp_path / "current.glb", paints={"A": a}, strip_marker=True, changed_image=True,
                image_name="authored texture", detached=True)
    result = check_current_print(path, tmp_path)
    assert result["ok"] and result["status"] == "not_required", result


@pytest.mark.parametrize("field", ["source_export", "production"])
@pytest.mark.parametrize("value", [None, [], [1], "bad", 7])
def test_malformed_application_objects_fail_closed(tmp_path, field, value):
    a = bake(tmp_path, "first", ["A"])
    path = mesh(tmp_path / "current.glb", paints={"A": a}, strip_marker=True)
    write(a["folder"] / "application.json", {**a["application"], field: value})
    result = check_current_print(path, tmp_path)
    assert not result["ok"] and result["status"] == "refused", result
    assert any(f"application.{field} must be a JSON object" in reason for reason in result["reasons"])


@pytest.mark.parametrize("field", ["node", "mesh", "primitive", "material", "pbr", "binding",
                                  "texture", "image", "buffer_view"])
def test_malformed_glb_object_fields_fail_closed(tmp_path, field):
    a = bake(tmp_path, "first", ["A"])
    path = mesh(tmp_path / "current.glb", paints={"A": a}, strip_marker=True)

    def edit(doc):
        if field in {"node", "mesh", "material", "texture", "image"}:
            doc["meshes" if field == "mesh" else field + "s"][0] = [1]
        elif field == "primitive":
            doc["meshes"][0]["primitives"][0] = [1]
        elif field == "pbr":
            doc["materials"][0]["pbrMetallicRoughness"] = [1]
        elif field == "binding":
            doc["materials"][0]["pbrMetallicRoughness"]["baseColorTexture"] = [1]
        else:
            doc["bufferViews"][doc["images"][0]["bufferView"]] = [1]

    edit_glb_json(path, edit)
    result = check_current_print(path, tmp_path)
    assert not result["ok"] and result["status"] == "refused", result
    assert any("must be a JSON object" in reason for reason in result["reasons"])


@pytest.mark.parametrize("extras", [[], [1], "custom metadata"])
def test_nonobject_extras_plain_allowed_but_active_print_refused(tmp_path, extras):
    a = bake(tmp_path, "first", ["A"])
    path = mesh(tmp_path / "current.glb")
    edit_glb_json(path, lambda doc: doc["nodes"][0].update(extras=extras))
    assert check_current_print(path, tmp_path)["status"] == "not_required"
    mesh(path, paints={"A": a}, strip_marker=True)
    edit_glb_json(path, lambda doc: doc["nodes"][0].update(extras=extras))
    result = check_current_print(path, tmp_path)
    assert not result["ok"] and result["status"] == "refused", result
