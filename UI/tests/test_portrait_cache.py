"""The cached portrait is an upload, not a preview, and every page agrees on it.

`cached_portrait` is what a restored session HANDS TO THE MODEL. Reload the
page, or arrive from another screen, and the file rebuilt from that entry is
what Nano Banana Pro renders from — so its resolution is picture quality, not a
thumbnail detail.

Five scripts wrote the key and no two agreed. Landing stored its 900px q0.82
preview and re-uploaded that: under half the 2048px the pipeline is built to
send, compression already baked in. The other four stored the untouched
original, which for a 12MP phone photo is 4-7MB of base64 against a ~5MB quota
— setItem threw, the catch swallowed it, and nothing was cached at all.

One helper in common.js now owns it. These tests keep it that way, because the
failure mode is silent on every path: no error, no log, just softer renders.
"""
import re
import unittest
from pathlib import Path

_JS = Path(__file__).resolve().parent.parent / "static" / "js"
_TEMPLATES = Path(__file__).resolve().parent.parent / "templates"

_KEY = "cached_portrait"
_HELPERS = ("cachePortrait", "restoredPortrait", "previewPortrait", "clearCachedPortrait")


def _read(p):
    return p.read_text(encoding="utf-8")


class TestOneOwnerForTheCache(unittest.TestCase):

    def test_common_defines_the_helpers(self):
        src = _read(_JS / "common.js")
        for fn in _HELPERS:
            self.assertIn(f"function {fn}(", src, f"common.js does not define {fn}")

    def test_only_common_names_the_key(self):
        """A page that touches sessionStorage directly is a page that can
        disagree about what goes in there, which is how this broke."""
        offenders = []
        for js in sorted(_JS.glob("*.js")):
            if js.name == "common.js":
                continue
            for i, line in enumerate(_read(js).splitlines(), 1):
                if _KEY in line:
                    offenders.append(f"{js.name}:{i}")
        self.assertEqual(offenders, [],
                         f"pages reaching past the helper into the cache: {offenders}")

    def test_no_page_hand_rolls_the_data_url_to_file_conversion(self):
        """Four copies of the same atob/Uint8Array/new File dance is four
        chances to reconstruct the upload differently."""
        offenders = []
        for js in sorted(_JS.glob("*.js")):
            if js.name == "common.js":
                continue
            src = _read(js)
            for m in re.finditer(r"new File\(\s*\[\s*u8\s*\]", src):
                line = src[:m.start()].count("\n") + 1
                offenders.append(f"{js.name}:{line}")
        self.assertEqual(offenders, [],
                         f"hand-rolled data-URL-to-File conversions: {offenders}")

    def test_the_cache_ceiling_matches_what_the_server_sends(self):
        """Caching above the pipeline's own ceiling wastes quota; below it
        throws away resolution the model would have used."""
        common = _read(_JS / "common.js")
        m = re.search(r"PORTRAIT_UPLOAD_MAX\s*=\s*(\d+)", common)
        self.assertIsNotNone(m, "common.js declares no PORTRAIT_UPLOAD_MAX")
        from UI.config import FS_MAX_IMAGE_DIM
        self.assertEqual(
            int(m.group(1)), FS_MAX_IMAGE_DIM,
            "the cached upload ceiling and the server's resize ceiling disagree",
        )

    def test_the_preview_is_smaller_than_the_upload(self):
        """Two sizes for two jobs. Collapsing them back into one is the bug."""
        common = _read(_JS / "common.js")
        upload = int(re.search(r"PORTRAIT_UPLOAD_MAX\s*=\s*(\d+)", common).group(1))
        preview = int(re.search(r"PORTRAIT_PREVIEW_MAX\s*=\s*(\d+)", common).group(1))
        self.assertLess(preview, upload)

    def test_the_fallback_chain_starts_at_the_ceiling_and_descends(self):
        common = _read(_JS / "common.js")
        steps = [int(s) for s in re.search(
            r"PORTRAIT_STEPS\s*=\s*\[([^\]]+)\]", common).group(1).split(",")]
        upload = int(re.search(r"PORTRAIT_UPLOAD_MAX\s*=\s*(\d+)", common).group(1))
        self.assertEqual(steps[0], upload, "the chain does not start at the ceiling")
        self.assertEqual(steps, sorted(steps, reverse=True), "the chain is not descending")
        self.assertGreater(len(steps), 1, "a chain of one is not a fallback")

    def test_every_page_using_a_helper_loads_common_first(self):
        """common.js is a plain script, not a module: order is the contract."""
        for tpl in sorted(_TEMPLATES.glob("*.html")):
            html = _read(tpl)
            scripts = re.findall(r'<script\s+src="/static/js/([A-Za-z0-9_.-]+)"', html)
            page_scripts = [s for s in scripts if s != "common.js"]
            uses = [s for s in page_scripts
                    if any(fn in _read(_JS / s) for fn in _HELPERS if (_JS / s).is_file())]
            if not uses:
                continue
            with self.subTest(page=tpl.name):
                self.assertIn("common.js", scripts, f"{tpl.name} uses the helpers without common.js")
                self.assertLess(scripts.index("common.js"), min(scripts.index(u) for u in uses),
                                f"{tpl.name} loads common.js after the script that calls it")


if __name__ == "__main__":
    unittest.main()
