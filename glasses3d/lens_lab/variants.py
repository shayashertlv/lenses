"""Isolated look-tool variants, using the exact source of an unapplied production port."""
import difflib
import inspect
from pathlib import Path
import types


LOOK_PATH = "glasses3d/agent/tools/look.py"


def regional_source():
    from glasses3d.agent.tools import look
    from . import regional

    original = Path(look.__file__).read_text(encoding="utf-8")
    helpers = "\n\n".join(inspect.getsource(fn).rstrip() for fn in
                           (regional.lens_region_stats, regional.regional_lens_colour_lines))
    measure = '                    row["colours"][role] = {"photo": colour_stats(photo, interior), "drawing": colour_stats(drawing, interior)}\n'
    assets = '        result["assets"] = '
    replacements = (
        ("def box_of(mask, margin=8):", helpers + "\n\n\ndef box_of(mask, margin=8):"),
        (measure, measure + '                    if role == "lens":\n'
                           '                        row["colours"][role]["regions"] = lens_region_stats(photo, drawing, interior)\n'),
        (assets, '        result["lens_colour_gap"] = regional_lens_colour_lines(rows, result["lens_colour_gap"])\n' + assets),
    )
    candidate = original
    for before, after in replacements:
        if candidate.count(before) != 1:
            raise ValueError("Production look source changed; review the regional port anchors")
        candidate = candidate.replace(before, after, 1)
    return original, candidate


def regional_module():
    from glasses3d.agent.tools import look

    module = types.ModuleType("glasses3d.agent.tools._lens_lab_regional_look")
    module.__package__ = look.__package__
    module.__file__ = str(Path(__file__).with_name("regional.py")) + " (production port)"
    exec(compile(regional_source()[1], module.__file__, "exec"), module.__dict__)
    return module


def build_variant_tools(scene, variant="baseline"):
    from glasses3d.agent.tools import build_tools

    if variant != "regional":
        # Historical grouping labels also retain the unmodified production path.
        return build_tools(scene)
    # Bind only this registry's look tool to its isolated module. No production
    # module globals are patched, even while the async tool runs in its worker thread.
    builder = types.FunctionType(build_tools.__code__,
                                 {**build_tools.__globals__, "look": regional_module()},
                                 build_tools.__name__, build_tools.__defaults__, build_tools.__closure__)
    builder.__kwdefaults__ = build_tools.__kwdefaults__
    return builder(scene)


def production_patch():
    original, candidate = regional_source()
    return "".join(difflib.unified_diff(original.splitlines(keepends=True), candidate.splitlines(keepends=True),
                                       fromfile="a/" + LOOK_PATH, tofile="b/" + LOOK_PATH))
