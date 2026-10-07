"""WP2 stage A: safe mode against Astra's real code (WP2_DESIGN §0, §13, §15 row A).

* corpus gate: 0 false positives on non-I/O lines across the 214 Studio snippets (186 Astra tool calls plus 28 host
  stop-checkpoint saves journaled under the same tool name, counted as the design counted them);
* chained replays of the three owner-accepted runs through the worker's validated exec (safe mode + every runtime
  layer), file-I/O statements removed and font loads replaced by the trusted load_font: every step's product equals
  the Studio's recorded after.blend exactly (Prada 19/19, Oakley 21/21, INVU 20/20; stop rule: any drop).

The corpus is data (`glasses3d/data/reference/astra_corpus/`, ignored: its manifest holds machine paths); it is built
from the Studio job folders when missing. Data-dependent tests skip, or fail under G3D_REQUIRE_DATA=1."""
from __future__ import annotations

import os

import pytest

from glasses3d.agent import astra_corpus as C
from glasses3d.g3d import safe_mode
from glasses3d.g3d_host import blender

REQUIRE_DATA = os.environ.get("G3D_REQUIRE_DATA") == "1"


def need(ok: bool, why: str) -> None:
    if not ok:
        (pytest.fail if REQUIRE_DATA else pytest.skip)(why)


@pytest.fixture(scope="module")
def rows():
    if not (C.CORPUS_DIR / "manifest.json").is_file() and any(p.is_dir() for p in C.SOURCES):
        C.build()
    found = C.load()
    need(bool(found), f"Astra corpus missing ({C.CORPUS_DIR}) and no Studio job folder to build it from")
    return found


def test_corpus_has_the_214_snippets(rows):
    astra = [r for r in rows if r["parent_tool"] == C.ASTRA]
    assert len(astra) == 214
    assert sum(r["context_parent"] == C.ASTRA for r in rows) == 186


def test_corpus_gate_has_no_false_positive(rows):
    g = C.gate(rows, safe_mode.validate)
    assert g["astra_snippets"] == 214
    assert g["false_positives"] == [], g["false_positives"][:5]
    assert g["refused"] == g["refused_io_only"]
    assert g["pass"] + g["refused"] == 214


def test_io_line_classes():
    assert C.io_class("bpy.ops.wm.save_as_mainfile(filepath=p, copy=True)") == "checkpoint"
    assert C.io_class("scene.render.filepath = out") == "self_render"
    assert C.io_class("notes = bpy.data.texts.new('n')") == "text_block"
    assert C.io_class("font = bpy.data.fonts.load(p)") == "font_load"
    assert C.io_class("im.pack()") == "pack_files"
    assert C.io_class("ob.location.x += 1") is None


def test_strip_io_removes_only_file_io_statements():
    code = ("import bpy\nob = bpy.data.objects['A']\nob.location.x = 1\n"
            "bpy.context.scene.render.filepath = 'C:/x.png'; ob.scale.x = 2\n"
            "try: bpy.ops.wm.save_as_mainfile(filepath='x.blend', copy=True)\nexcept Exception: pass\n"
            "bpy.ops.render.render(write_still=True)\n")
    out = C.strip_io(code)
    assert safe_mode.validate(out) == []
    assert "ob.scale.x = 2" in out and "ob.location.x = 1" in out
    assert "filepath" not in out and "render.render" not in out


def test_font_loads_become_trusted_load_font_names():
    code = "try: font=bpy.data.fonts.load('C:\\\\Windows\\\\Fonts\\\\timesbd.ttf')\nexcept: font=None"
    assert C.font_loads(code) == ["timesbd"]
    out = C.replace_font_loads(code, {"timesbd": "Times New Roman Bold"})
    assert "bpy.data.fonts['Times New Roman Bold']" in out and safe_mode.validate(out) == []


def test_accepted_runs_have_their_step_counts(rows):
    for product, (_, _, steps) in C.ACCEPTED_RUNS.items():
        found = C.accepted_run_steps(rows, product)
        need(len(found) > 0 and all(r["before_blend"] and r["after_blend"] for r in found),
             f"{product}: Studio run folder missing")
        assert len(found) == steps, product


@pytest.mark.slow
@pytest.mark.skipif(blender.blender_exe() is None, reason="Blender 5.2 not installed")
@pytest.mark.parametrize("product", ["prada", "oakley", "invu"])
def test_chained_replay_rebuilds_the_accepted_run_exactly(product, rows, tmp_path):
    steps = C.accepted_run_steps(rows, product)
    need(len(steps) == C.ACCEPTED_RUNS[product][2] and all(r["before_blend"] and r["after_blend"] for r in steps),
         f"{product}: Studio run folder missing")
    result = C.replay_chain(product, tmp_path, rows)
    bad = [r for r in result["results"] if not r["equal"]]
    assert result["steps"] == result["expected_steps"] == C.ACCEPTED_RUNS[product][2]
    assert result["equal_steps"] == result["steps"], bad[:3]
    assert result["final_equal"]
    for r in result["results"]:               # Astra's code ran as sent or with only its file I/O removed
        assert r["mode"] in ("as_sent", "io_stripped", "load_font", "load_font+io_stripped",
                             "as_sent_refused_in_studio"), r
        if r["studio_status"] == "returned":
            assert r["status"] == "ok", r
