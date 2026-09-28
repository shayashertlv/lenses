"""The offline demo: synthetic inputs and a complete scripted author session on the synthetic worker.

Nothing here is a product: the "photos" are generated PNGs (one of them sealed), the worker returns labelled
synthetic images and a deliberately failing first build, the scripted transport plays a realistic author who lists
the evidence, writes a program, repairs the failure, receives the images, asks the critic, selects and delivers, and
scripted critic/final answers close the job. The result proves the state machine, not any glasses.
"""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw

from .responses import canonical


def synthetic_photos(folder: Path) -> list[dict]:
    """Four distinct generated images; the angled one is held out (magenta, so a leak is visible in any payload)."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for i, (view, colour, held) in enumerate((("front", (230, 230, 230), False), ("back", (210, 220, 230), False), ("left", (220, 230, 210), False),
                                                ("angled", (255, 0, 255), True))):
        im = Image.new("RGB", (320, 200), colour)
        d = ImageDraw.Draw(im)
        d.rounded_rectangle([40, 60, 150, 140], radius=20, outline=(40, 40, 60), width=6)
        d.rounded_rectangle([170, 60, 280, 140], radius=20, outline=(40, 40, 60), width=6)
        d.line([150, 90, 170, 90], fill=(40, 40, 60), width=6)
        d.text((8, 8), f"SYNTHETIC {view} photo #{i}", fill=(0, 0, 0))
        p = folder / f"{view}.png"
        im.save(p)
        out.append({"path": str(p), "view": view, "held_out": held})
    return out


def demo_request(folder: Path) -> dict:
    return {"product_id": "demo-synthetic", "photos": synthetic_photos(folder), "dimensions": {}, "held_out_views": ["angled"],
            "notes": "SYNTHETIC demo product: generated images, no real glasses. The worker is synthetic; nothing built here is an asset."}


PROGRAM_A = "gl.note('demo program A')\nfront = gl.box((0, 0, 0), (140, 50, 6), 'front', 'frame', 'front')\n"
PROGRAM_B = PROGRAM_A.replace("program A", "program B (repaired)") + "gl.set_bridge_underside((0, -7, -3))\n"


def _resp(call_id: str, name: str, args: dict, *, rs: str) -> dict:
    return {"id": f"resp_{call_id}", "object": "response", "status": "completed", "model": "gpt-6-astra",
            "usage": {"input_tokens": 1200, "input_tokens_details": {"cached_tokens": 800, "cache_write_tokens": 100}, "output_tokens": 300,
                      "output_tokens_details": {"reasoning_tokens": 200}, "total_tokens": 1500},
            "output": [{"type": "reasoning", "id": rs, "summary": [], "encrypted_content": "opaque-" + rs},
                       {"type": "message", "id": "msg_" + call_id, "role": "assistant", "status": "completed", "phase": "commentary",
                        "content": [{"type": "output_text", "text": f"Calling {name}.", "annotations": []}]},
                       {"type": "function_call", "id": "fc_" + call_id, "call_id": call_id, "name": name, "status": "completed", "arguments": json.dumps(args)}]}


def modules(frame: str) -> dict:
    return {n: {"mode": "inherit", "content": None, "expected_base_sha256": None} for n in ("setup", "lenses", "temples", "hardware", "materials", "finish")} | \
           {"frame": {"mode": "replace", "content": frame, "expected_base_sha256": None}}


def sealed_pixel_hashes(photos: list[dict]) -> list[str]:
    """Decoded-pixel hashes of the held-out demo photos: the leak sentinel every author/critic payload is checked against."""
    import hashlib
    out = []
    for p in photos:
        if p.get("held_out"):
            with Image.open(p["path"]) as im:
                out.append(hashlib.sha256(im.convert("RGBA").tobytes()).hexdigest())
    return out


def demo_script(sealed: list[str] | None = None) -> list[dict]:
    """The scripted transport steps of the demo, in the order the runner sends requests. ``sealed``: pixel hashes no
    author or critic payload may carry."""
    sealed = list(sealed or [])
    critic = {"defects": [{"part": "frame", "where_seen": "synthetic render tex_front", "description": "synthetic image; nothing to judge", "severity": "minor"}],
              "matches": ["synthetic"], "uncertainty": "total: synthetic images", "suggested_repairs": [], "summary": "scripted critic answer"}
    final = {"discrepancies": [], "identity_checklist": [], "resemblance_0_10": {"front": 5, "side": 5, "angled_held_out": 5, "materials_and_lenses": 5, "mirror_overall": 5},
             "runtime_notes": "scripted", "overall": "reject", "summary": "scripted final answer on a synthetic revision"}
    return [
        {"endpoint": "responses", "expect": {"tools": list(__import__("modeler.agentic.tools", fromlist=["TOOLS"]).TOOLS), "has_image": True, "no_sealed_pixels": sealed},
         "body": _resp("call_1", "list_evidence", {}, rs="rs_1")},
        {"endpoint": "responses", "expect": {"contains_call_output": "call_1"},
         "body": _resp("call_2", "edit_program", {"base_revision_id": None, "modules": modules(PROGRAM_A), "rationale": "first construction", "expected_changes": ["a front"],
                                                  "build_now": True, "deliver_if_compatible": False}, rs="rs_2")},
        {"endpoint": "responses", "expect": {"contains_call_output": "call_2"},
         "body": _resp("call_3", "edit_program", {"base_revision_id": "r0001", "modules": modules(PROGRAM_B), "rationale": "repair after the scripted failure",
                                                  "expected_changes": ["builds"], "build_now": True, "deliver_if_compatible": False}, rs="rs_3")},
        # the repaired build produced more renders than one message carries: the author must fetch the remainder
        {"endpoint": "responses", "expect": {"contains_call_output": "call_3", "has_image": True, "has_text": ["deferred_images"]},
         "body": _resp("call_3b", "fetch_pending_images", {"max_images": 6}, rs="rs_3b")},
        {"endpoint": "responses", "expect": {"contains_call_output": "call_3b", "has_image": True},
         "body": _resp("call_4", "request_critic", {"revision_id": "r0002", "question": "what is visibly wrong?"}, rs="rs_4")},
        {"endpoint": "responses", "expect": {"tools": ["report_critique"], "has_image": True, "no_sealed_pixels": sealed},
         "body": _resp("call_c", "report_critique", critic, rs="rs_c")},
        {"endpoint": "responses", "expect": {"contains_call_output": "call_4"},
         "body": _resp("call_5", "select_revision", {"revision_id": "r0002"}, rs="rs_5")},
        {"endpoint": "responses", "expect": {"contains_call_output": "call_5"},
         "body": _resp("call_6", "request_delivery", {"revision_id": "r0002", "status_claim": "best_effort", "note": "scripted delivery of the synthetic revision"}, rs="rs_6")},
        {"endpoint": "responses", "expect": {"tools": ["report_evaluation"]},
         "body": _resp("call_f", "report_evaluation", final, rs="rs_f")},
    ]


def demo_fake_scenario() -> dict:
    """Operation 1 (the first build) fails deliberately; everything else succeeds."""
    return {1: {"ok": False, "error": "SyntheticError: scripted build failure in module frame"}}


def write_demo_script(path: Path, photos: list[dict] | None = None) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical({"steps": demo_script(sealed_pixel_hashes(photos or [])), "fake_scenario": {str(k): v for k, v in demo_fake_scenario().items()}}))
    return path
