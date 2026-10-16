"""Short, stable owner labels, independent of the immutable mesh identifiers."""
from __future__ import annotations

import re
from PIL import Image, ImageDraw


PREFIX = {"object": "O", "face_region": "T", "edge_chain": "E", "point": "P"}


def resolve(key, targets, labels):
    # Exact mesh IDs remain the authority; owner labels only name those IDs.
    if key in targets:
        return key
    matches = [target_id for target_id, row in labels.items() if row["label"] == key]
    if len(matches) != 1 or matches[0] not in targets:
        raise ValueError(f"Unknown displayed map label: {key}")
    return matches[0]


def description(value, obj):
    side = {1: "left", -1: "right"}.get(value.get("side", obj.get("side")))
    part = (f"{side} arm" if side else "arm") if obj["role"] == "temple" else obj["role"] or "part"
    names = [re.sub(r"[_|]+", " / ", name) for name in value.get("aliases", [])]
    if value["kind"] == "object":
        return f"{part}: {value.get('object_name', obj.get('name', 'part')).replace('_', ' ')}"
    kind = {"face_region": "surface", "edge_chain": "border", "point": "corner"}[value["kind"]]
    return f"{kind}{' ' + ', '.join(names) if names else ''}, {part}"


def allocate(value, obj, labels, descriptions):
    key = value["id"]
    if key not in labels:
        prefix = PREFIX[value["kind"]]
        number = 1 + max((int(row["label"][1:]) for row in labels.values()
                          if row["label"].startswith(prefix)), default=0)
        labels[key] = {"label": f"{prefix}{number}", "target_id": key,
                       "description": descriptions.get(key, description(value, obj))}
    elif key in descriptions and descriptions[key] != labels[key]["description"]:
        raise ValueError("A displayed label's description is fixed for this map; create a new map to rename it")
    return labels[key]


def validate_descriptions(value, targets, labels):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("label_descriptions_json must be an object of target IDs and plain descriptions")
    result = {}
    for key, text in value.items():
        if not isinstance(text, str) or not text.strip() or len(text) > 180 or any(ord(c) < 32 for c in text):
            raise ValueError("Each label description must be one readable line of 1–180 characters")
        result[resolve(key, targets, labels)] = text.strip()
    return result


def append_legend(canvas, rows, font):
    """Keep a complete readable legend on every full pair and native tile, without shrinking pixels."""
    if not rows:
        return canvas
    draw = ImageDraw.Draw(canvas)
    lines = []
    width = max(40, canvas.width - 24)
    for row in rows:
        words = f"{row['label']} = {row['description']}".split()
        # Even a long mesh name without spaces must stay visible.
        chunks = []
        for word in words:
            chunk = ""
            for char in word:
                if chunk and draw.textbbox((0,0),chunk+char,font=font)[2] > width:
                    chunks.append(chunk)
                    chunk = ""
                chunk += char
            chunks.append(chunk)
        line = ""
        for word in chunks:
            proposed = (line + " " + word).strip()
            if line and draw.textbbox((0, 0), proposed, font=font)[2] > width:
                lines.append(line)
                line = word
            else:
                line = proposed
        lines.append(line)
    height = max(24, font.getbbox("Ag")[3] + 8)
    result = Image.new("RGB", (canvas.width, canvas.height + height * len(lines) + 16), "white")
    result.paste(canvas, (0, 0))
    draw = ImageDraw.Draw(result)
    for i, line in enumerate(lines):
        draw.text((12, canvas.height + 6 + i * height), line, font=font, fill="black")
    return result
