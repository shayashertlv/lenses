"""The HTML/JS contract: ids the JS reaches for must actually exist.

There is no other automated coverage of the frontend. 134 ids are defined
across the four templates and 69 are looked up by id from JS, and nothing
catches a rename until someone clicks the thing at runtime. These tests are
the only guard on a large redesign.
"""
import re
import unittest
from pathlib import Path

_UI = Path(__file__).resolve().parent.parent
_TEMPLATES = _UI / "templates"
_JS = _UI / "static" / "js"

# Created by JS at runtime rather than declared in markup, so they will never
# appear in a template. ensureLiveRegions() injects both into <body>.
RUNTIME_INJECTED = {"sf-live", "sf-alert"}

_ID_ATTR = re.compile(r'\sid="([A-Za-z0-9_-]+)"')
_GET_BY_ID = re.compile(r"""getElementById\(\s*['"]([A-Za-z0-9_-]+)['"]\s*\)""")
_QUERY_ID = re.compile(r"""querySelector(?:All)?\(\s*['"]#([A-Za-z0-9_-]+)['"]""")
_SCRIPT_SRC = re.compile(r'<script\s+src="/static/js/([A-Za-z0-9_.-]+)"')


def _read(path):
    return path.read_text(encoding="utf-8")


def _ids_in(text):
    """Every id this source declares, including ones built inside JS template literals."""
    return set(_ID_ATTR.findall(text))


def _ids_referenced(text):
    return set(_GET_BY_ID.findall(text)) | set(_QUERY_ID.findall(text))


def _page_map():
    """template stem -> (ids available to it, ids its scripts reference)."""
    pages = {}
    for tpl in sorted(_TEMPLATES.glob("*.html")):
        html = _read(tpl)
        scripts = [_JS / name for name in _SCRIPT_SRC.findall(html)]
        available = _ids_in(html) | RUNTIME_INJECTED
        referenced = set()
        for js in scripts:
            if not js.is_file():
                continue
            src = _read(js)
            referenced |= _ids_referenced(src)
            # Elements this script creates itself count as defined.
            available |= _ids_in(src)
        pages[tpl.stem] = (available, referenced, [p.name for p in scripts])
    return pages


class TestIdContract(unittest.TestCase):

    def test_every_referenced_id_exists_on_its_page(self):
        broken = {}
        for page, (available, referenced, scripts) in _page_map().items():
            missing = sorted(referenced - available)
            if missing:
                broken[page] = (missing, scripts)
        self.assertEqual(
            broken, {},
            "JS looks up ids that no template or script defines. Each one is a "
            "silent TypeError on the live page:\n" + "\n".join(
                f"  {page}: {missing} (from {scripts})" for page, (missing, scripts) in broken.items()
            ),
        )

    def test_scripts_referenced_by_templates_exist(self):
        for tpl in sorted(_TEMPLATES.glob("*.html")):
            for name in _SCRIPT_SRC.findall(_read(tpl)):
                self.assertTrue((_JS / name).is_file(), f"{tpl.name} loads missing script {name}")

    def test_every_template_has_a_stylesheet(self):
        css_dir = _UI / "static" / "css"
        for tpl in sorted(_TEMPLATES.glob("*.html")):
            hrefs = re.findall(r'<link[^>]+href="/static/css/([A-Za-z0-9_.-]+)"', _read(tpl))
            self.assertTrue(hrefs, f"{tpl.name} loads no stylesheet")
            for name in hrefs:
                self.assertTrue((css_dir / name).is_file(), f"{tpl.name} loads missing stylesheet {name}")


class TestSelectionInvariant(unittest.TestCase):
    """The facet controls' selected state depends on markup order.

    free-search.css and lens-recolor.css style selection with adjacent-sibling
    rules (input:checked + .tile-inner). The hidden <input> must stay the
    immediate previous sibling of the styled div or every selection across
    ~110 controls stops rendering, silently, with nothing thrown.

    Once the :has() migration lands this stops being load-bearing and becomes
    an ordinary regression guard.
    """

    SIBLING_PAIRS = [("tile-inner",), ("swatch-inner",), ("color-pick-inner",)]

    def test_hidden_input_immediately_precedes_its_styled_div(self):
        offenders = []
        for tpl in sorted(_TEMPLATES.glob("*.html")):
            html = _read(tpl)
            for (inner,) in self.SIBLING_PAIRS:
                for m in re.finditer(r'<div class="[^"]*\b%s\b' % re.escape(inner), html):
                    before = html[max(0, m.start() - 400):m.start()]
                    tags = re.findall(r"<(\w+)", before)
                    if not tags or tags[-1] != "input":
                        snippet = before[-90:].replace("\n", " ")
                        offenders.append(f"{tpl.name}: .{inner} not preceded by <input> ...{snippet}")
        self.assertEqual(offenders, [], "Selection feedback will not render:\n" + "\n".join(offenders))


if __name__ == "__main__":
    unittest.main()
