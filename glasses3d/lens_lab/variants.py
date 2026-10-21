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


def _rebind(function, namespace):
    rebound = types.FunctionType(function.__code__, namespace, function.__name__,
                                 function.__defaults__, function.__closure__)
    rebound.__kwdefaults__ = function.__kwdefaults__
    rebound.__annotations__ = function.__annotations__
    return rebound


def _capture_modules(variant, capture_runner):
    from glasses3d.agent.tools import arview, look

    if variant == "regional":
        look_module = regional_module()
    else:
        look_module = types.ModuleType("glasses3d.agent.tools._lens_lab_baseline_look")
        look_module.__dict__.update(look.__dict__)
    if capture_runner is not None:
        arview_module = types.ModuleType("glasses3d.agent.tools._lens_lab_arview")
        arview_module.__dict__.update(arview.__dict__)
        arview_module.run_capture = capture_runner
        arview_module.capture = _rebind(arview.capture, arview_module.__dict__)
        look_module.arview = arview_module
        look_module.capture = _rebind(look_module.capture, look_module.__dict__)
        look_module.tool_for = _rebind(look_module.tool_for, look_module.__dict__)
    else:
        arview_module = arview
    return look_module, arview_module


def build_variant_tools(scene, variant="baseline", *, capture_runner=None):
    from glasses3d.agent.tools import build_tools
    from glasses3d.agent.tools import lens_candidates

    if variant not in {"regional", "pre14"} and capture_runner is None:
        # Historical grouping labels also retain the unmodified production path.
        return build_tools(scene)
    # Bind only this registry's look tool to its isolated module. No production
    # module globals are patched, even while the async tool runs in its worker thread.
    look_module, arview_module = _capture_modules(variant, capture_runner)
    namespace = {**build_tools.__globals__, "look": look_module, "arview": arview_module}
    candidate_module = types.ModuleType("glasses3d.agent.tools._lens_lab_candidates")
    candidate_module.__dict__.update(lens_candidates.__dict__)
    candidate_module.arview, candidate_module.look = arview_module, look_module
    for name in ("_worn", "_front", "capture", "tool_for"):
        setattr(candidate_module, name, _rebind(getattr(lens_candidates, name), candidate_module.__dict__))
    namespace["lens_candidates"] = candidate_module
    if variant == "pre14":
        from . import pre14
        report_tool = namespace["report_tool"]
        namespace["report_tool"] = _rebind(report_tool, {**report_tool.__globals__, "read_report": pre14.read_report})
    registry = _rebind(build_tools, namespace)(scene)
    if variant == "pre14":
        for tool in registry:
            if tool.name == "compare_look_to_photos":
                invoke = tool.on_invoke_tool
                async def without_gap(ctx, arguments, invoke=invoke):
                    return pre14.strip_tool_output(await invoke(ctx, arguments))
                tool.on_invoke_tool = without_gap
    return registry


def production_patch():
    original, candidate = regional_source()
    return "".join(difflib.unified_diff(original.splitlines(keepends=True), candidate.splitlines(keepends=True),
                                       fromfile="a/" + LOOK_PATH, tofile="b/" + LOOK_PATH))
