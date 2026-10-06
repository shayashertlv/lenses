"""The instrument never imports photoprint (WP5_DESIGN §7 independence): parsed and checked in a fresh interpreter."""
import ast
import subprocess
import sys
from pathlib import Path

INSTRUMENT = Path(__file__).resolve().parents[1] / "instruments" / "print_metrics.py"
REPO = Path(__file__).resolve().parents[3]


def test_print_metrics_source_never_names_photoprint():
    tree = ast.parse(INSTRUMENT.read_text(encoding="utf-8"))
    mods = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            mods.append(node.module or "")
            mods += [f"{node.module}.{a.name}" for a in node.names]
    assert mods, "the instrument imports something"
    assert not [m for m in mods if "photoprint" in m], mods


def test_importing_print_metrics_loads_no_photoprint_module():
    code = ("import sys; import glasses3d.tests.instruments.print_metrics as m; "
            "bad = [k for k in sys.modules if 'photoprint' in k]; print(bad); sys.exit(1 if bad else 0)")
    r = subprocess.run([sys.executable, "-B", "-c", code], cwd=str(REPO), capture_output=True, text=True,
                       env={**__import__("os").environ, "PYTHONPATH": str(REPO)})
    assert r.returncode == 0, r.stdout + r.stderr
