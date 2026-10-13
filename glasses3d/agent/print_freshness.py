"""Bind active exported prints to retained successful job bakes, including restored heads.

This is an assembly integrity check, not a new appearance or coverage gate. Every
active bake is checked with the existing full manifest checker. A partially
detached/replaced old bake group must be completely detached or reprinted.
"""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import re

from ..g3d.printuv import glbmesh as GM
from ..g3d_host.photoprint import manifest as MF

ATTACHMENT_SCHEMA = "g3d.print_attachment/1"
IDENTITY_KEYS = ("print_sha256", "geometry_sha", "charts_sha")
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_IMAGE_NAME = re.compile(r"g3d print [0-9a-f]{12}(?:\.\d+)?\Z")


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _identity(record):
    if not isinstance(record, dict):
        return None
    values = tuple(record.get(k) for k in IDENTITY_KEYS)
    return values if all(isinstance(x, str) and _HEX.fullmatch(x) for x in values) else None


def _pixels(data):
    from PIL import Image
    try:
        with Image.open(io.BytesIO(data)) as image:
            rgba = image.convert("RGBA")
            return _sha(f"{rgba.width}x{rgba.height}:".encode() + rgba.tobytes())
    except (OSError, ValueError):
        return None


def _local(root, value):
    if not isinstance(value, str) or not value:
        raise ValueError("missing job-local evidence path")
    path = Path(value)
    path = (path if path.is_absolute() else root / path).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError("print evidence is missing or outside the job")
    return path


def _json(path):
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise ValueError("print evidence must be a JSON object")
    return value


def _object(value, label):
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _bakes(root):
    out = []
    directory = root / "work" / "print"
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob("*/print_manifest.json")):
        record = {"manifest": str(path), "errors": []}
        try:
            path = _local(root, str(path))
            man = _json(path)
            record.update(data=man, identity=_identity(man), manifest_sha256=_sha(path.read_bytes()))
            if record["identity"] is None:
                raise ValueError("invalid print manifest identity")
            MF.texture_eligibility(man, man["parts"])
            png = _local(root, man.get("print_png"))
            raw = png.read_bytes()
            if _sha(raw) != man["print_sha256"]:
                raise ValueError("print.png changed since the bake")
            record["pixels"] = _pixels(raw)
            source = _local(root, man.get("glb"))
            if _sha(source.read_bytes()) != man.get("glb_sha256"):
                raise ValueError("source GLB changed since the bake")
            application = path.parent / "application.json"
            app = _json(_local(root, str(application)))
            record["application"] = str(application)
            if app.get("status") != "ok":
                raise ValueError("the bake has no successful application")
            if app.get("manifest_sha256") != record["manifest_sha256"]:
                raise ValueError("manifest differs from its successful application")
            source_export = _object(app.get("source_export"), "application.source_export")
            production = _object(app.get("production"), "application.production")
            if source_export.get("sha256") != man["glb_sha256"]:
                raise ValueError("application source differs from the bake")
            if production.get("print_sha256") != man["print_sha256"]:
                raise ValueError("application print differs from the bake")
            if app.get("texture_parts") != man["texture_parts"]:
                raise ValueError("application eligibility differs from the bake")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            record["errors"].append(str(exc))
        out.append(record)
    return out


def check_current_print(glb_path, root) -> dict:
    """Fresh per-export check; never trust a cached application export.print_check.

    No active print is ``not_required``. Existing print-UV metadata plus actual
    material sampling identify attachments; retained print-image bytes/pixels
    and packed image names detect bindings whose UV metadata was removed too.
    """
    root = Path(root).resolve()
    reasons, attachments = [], []
    result = {"ok": False, "status": "refused", "glb_sha256": None,
              "reasons": reasons, "attachments": attachments}
    try:
        raw = Path(glb_path).read_bytes()
        result["glb_sha256"] = _sha(raw)
        doc, binary = GM.chunks(raw)
        _object(doc, "GLB document")
        bakes = _bakes(root)
        image_facts = {}
        claims = []
        for node in doc.get("nodes", []):
            _object(node, "GLB node")
            name = node.get("name", "<unnamed>")
            # glTF permits arbitrary JSON extras. Only object extras can contain
            # our metadata; an actual print image still identifies the binding.
            extras = node.get("extras")
            raw_info = extras.get(MF.PRINT_UV_KEY) if isinstance(extras, dict) else None
            info = json.loads(raw_info) if isinstance(raw_info, str) else raw_info
            marker = info.get("attachment") if isinstance(info, dict) else None
            marker_id = _identity(marker)
            if marker is not None and (marker_id is None or marker.get("schema") != ATTACHMENT_SCHEMA):
                reasons.append(f"{name}: malformed print attachment identity")
            possible = {marker_id} if marker_id is not None else set()
            active = marker is not None
            mesh = doc.get("meshes", [])[node["mesh"]] if "mesh" in node else {}
            _object(mesh, "GLB mesh")
            for primitive in mesh.get("primitives", []):
                _object(primitive, "GLB primitive")
                material = doc.get("materials", [])[primitive["material"]] if "material" in primitive else {}
                _object(material, "GLB material")
                pbr = _object(material.get("pbrMetallicRoughness", {}), "GLB pbrMetallicRoughness")
                binding = pbr.get("baseColorTexture")
                if binding is None:
                    continue
                _object(binding, "GLB baseColorTexture")
                print_uv_binding = (isinstance(info, dict) and info.get("layer") == MF.LAYER
                                    and type(info.get("texcoord")) is int
                                    and binding.get("texCoord", 0) == info["texcoord"])
                if print_uv_binding:
                    active = True
                    if marker_id is None:
                        possible.update(b["identity"] for b in bakes if b.get("identity") is not None
                                        and b["identity"][1:] == (info.get("geometry_sha"), info.get("charts_sha")))
                texture = doc.get("textures", [])[binding["index"]]
                _object(texture, "GLB texture")
                index = texture.get("source")
                if index is None:
                    continue  # specification/manifest checks report unsupported image sources
                if index not in image_facts:
                    image = doc.get("images", [])[index]
                    _object(image, "GLB image")
                    if "bufferView" in image:
                        _object(doc["bufferViews"][image["bufferView"]], "GLB image bufferView")
                    payload = MF._image_bytes(doc, binary, index)
                    image_facts[index] = {"name": str(image.get("name", "")),
                                          "sha": _sha(payload) if payload is not None else None,
                                          "pixels": _pixels(payload) if payload is not None else None}
                fact = image_facts[index]
                matches = {b["identity"] for b in bakes if b.get("identity") is not None
                           and (fact["sha"] == b["identity"][0]
                                or fact["pixels"] is not None and fact["pixels"] == b.get("pixels"))}
                if print_uv_binding or matches or _IMAGE_NAME.fullmatch(fact["name"]):
                    active = True
                    if marker_id is None:
                        if isinstance(info, dict):
                            matches = {key for key in matches if key[1:] ==
                                       (info.get("geometry_sha"), info.get("charts_sha"))}
                        possible.update(matches)
            if active:
                claims.append((name, possible))
        verified = {}
        for name, possible in claims:
            failures = []
            found = False
            for identity in sorted(possible):
                if identity in verified:
                    if name in verified[identity]["parts"]:
                        found = True
                        break
                    failures.append("the attached object is not an eligible part of that bake")
                    continue
                candidates = [b for b in bakes if b.get("identity") == identity]
                for bake in candidates:
                    if bake["errors"]:
                        failures.extend(bake["errors"])
                        continue
                    if name not in bake["data"]["texture_parts"]:
                        failures.append("the attached object is not an eligible part of that bake")
                        continue
                    try:
                        check = MF.check_glb(glb_path, bake["data"])
                    except AttributeError as exc:
                        # The existing manifest reader also parses nested JSON
                        # (including arbitrary extras). Keep malformed shapes at
                        # that parser boundary as a refusal, never an export crash.
                        raise ValueError(f"manifest could not read print evidence: {exc}") from exc
                    if not check.get("ok"):
                        failures.extend(check.get("reasons", ["manifest check failed"]))
                        continue
                    row = {"identity": dict(zip(IDENTITY_KEYS, identity)), "manifest": bake["manifest"],
                           "manifest_sha256": bake["manifest_sha256"], "application": bake["application"],
                           "parts": bake["data"]["texture_parts"], "check": check}
                    attachments.append(row)
                    verified[identity] = row
                    found = True
                    break
                if found:
                    break
            if not found:
                why = "; ".join(dict.fromkeys(failures)) or "no matching successful retained bake"
                reasons.append(f"{name}: print stale or unbound: {why}. Reprint or detach the complete old bake group.")
        result.update(ok=not reasons, status="refused" if reasons else "verified" if claims else "not_required")
    except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
        reasons.append(f"print freshness could not be verified: {type(exc).__name__}: {exc}")
    return result
