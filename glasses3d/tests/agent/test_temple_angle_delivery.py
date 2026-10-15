"""A visual choice becomes selectable only after its actual bounded SDK image output succeeds."""
import base64
from pathlib import Path

import pytest
from PIL import Image

from glasses3d.agent import loop, tool_reports
from glasses3d.agent.temple_lab import crease_angles
from glasses3d.tests.agent.test_temple_lab_v3 import FakeLab, call, tools_of


ARGS = {"parent_revision": 0, "facets": ["front", "side"], "moving_facet": "front", "shape": "ridge",
        "angles_deg": [6., 11., 16.], "photos": ["05"], "judgement": None}


def fixture_lab(tmp_path):
    image = tmp_path / "angles.png"
    Image.new("RGB", (1600, 300), "orange").save(image)
    lab = FakeLab({"status": "ok", "comparison_id": "crease001", "images": [str(image)],
                   "diagnostics": "A full lossless diagnostic. " * 1500})
    lab.root = tmp_path
    lab.delivered = []
    lab.mark_delivered = lambda cid, tool: lab.delivered.append((cid, tool))
    return lab, image


def test_delivery_occurs_after_output_limiting_with_exact_image_bytes(tmp_path):
    lab, image = fixture_lab(tmp_path)
    result = call(tools_of(lab)["facet_crease_compare"], ARGS, "angle-call")
    images = [part for part in result if getattr(part, "type", None) == "image"]
    assert len(images) == 1
    assert base64.b64decode(images[0].image_url.split(",", 1)[1]) == image.read_bytes()
    assert sum(len(part.text) for part in result if hasattr(part, "text")) <= tool_reports.TEXT_LIMIT
    assert lab.delivered == [("angle-call", "facet_crease_compare")] and not lab.unseen


def test_failed_outer_output_limiter_cannot_mark_comparison_delivered(tmp_path, monkeypatch):
    lab, _ = fixture_lab(tmp_path)
    tool = tools_of(lab)["facet_crease_compare"]

    def refuse(*args):
        raise ValueError("cannot write final report")

    monkeypatch.setattr(tool_reports, "limit_output", refuse)
    with pytest.raises(ValueError, match="final report"):
        call(tool, ARGS, "lost-call")
    assert lab.delivered == [] and "lost-call" in lab.unseen


def test_new_composite_deadlines_cover_work_and_mandatory_restore():
    assert loop.HARD_TIMEOUT_S["facet_crease_compare"] >= crease_angles.COMPARE_WORK_DEADLINE_S + 90 + 90 + 60
    assert loop.HARD_TIMEOUT_S["facet_visible_angle"] >= 390
    assert loop.sdk_timeout_s("facet_crease_compare") > loop.HARD_TIMEOUT_S["facet_crease_compare"]
