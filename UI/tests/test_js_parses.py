"""Every shipped script must actually parse.

This exists because free-search.js was broken for several commits and the
whole suite stayed green: a stray quote inside an interpolated onclick made
the file a syntax error, so every function in it — the faceting, the card
builder, the poller — silently did not exist. Nothing else here reads
JavaScript, so nothing noticed.

Uses `node --check`, which parses without executing. Skips when node is not on
PATH rather than failing, so the suite stays runnable on a machine without it.
"""
import shutil
import subprocess
import unittest
from pathlib import Path

_JS_DIR = Path(__file__).resolve().parent.parent / "static" / "js"
_NODE = shutil.which("node")


@unittest.skipIf(_NODE is None, "node is not on PATH; cannot parse-check the scripts")
class TestScriptsParse(unittest.TestCase):

    def test_every_script_parses(self):
        scripts = sorted(_JS_DIR.glob("*.js"))
        self.assertTrue(scripts, "no scripts found to check")
        broken = []
        for js in scripts:
            r = subprocess.run([_NODE, "--check", str(js)],
                               capture_output=True, text=True, timeout=30)
            if r.returncode != 0:
                first = next((ln for ln in r.stderr.splitlines()
                              if "Error" in ln or "^" in ln), r.stderr.strip()[:200])
                broken.append(f"{js.name}: {first.strip()}")
        self.assertEqual(broken, [], "scripts that do not parse:\n" + "\n".join(broken))


class TestNoInterpolatedHandlers(unittest.TestCase):
    """An onclick built inside a JS string is how the parse error got in, and
    how the earlier attribute-injection bug got in. Use listeners."""

    def test_no_onclick_built_inside_a_js_string(self):
        offenders = []
        for js in sorted(_JS_DIR.glob("*.js")):
            for i, line in enumerate(js.read_text(encoding="utf-8").splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith(("*", "//", "/*")):
                    continue
                if "onclick=" in line and ("'" in line.split("onclick=")[0][-3:] or '"' in line.split("onclick=")[0][-3:] or "+" in line):
                    if "addEventListener" not in line:
                        offenders.append(f"{js.name}:{i}")
        self.assertEqual(offenders, [],
                         "onclick interpolated into a JS string; use a listener or a data attribute:\n"
                         + "\n".join(offenders))


if __name__ == "__main__":
    unittest.main()
