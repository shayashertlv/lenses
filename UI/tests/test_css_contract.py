"""Ratchet budgets for the CSS layer.

Every number below is a ceiling measured against the shipped state. Lower them
as work lands, never raise them. A test that fails because a budget got smaller
is the point.

Responsive breakage was one of this repo's chronic failure modes. The measured
cause was two orphan breakpoints: 540px lived only in free-search.css and 768px
only in common.css, so parts of a page reflowed at widths nothing else used.
Both are gone; the whole app now runs on 560/900.
"""
import re
import unittest
from pathlib import Path

_UI = Path(__file__).resolve().parent.parent
_CSS = _UI / "static" / "css"
_TEMPLATES = _UI / "templates"

# --- budgets. Lower these, never raise. ---
# The whole app runs on 560/900, down from 380/540/600/768/900/1024.
BREAKPOINT_BUDGET = 3
# Zero: every page is on the token layer and the 560/900 set.
ORPHAN_BREAKPOINT_BUDGET = 0
# 237 -> 186. What remains is facet geometry (clip-path silhouettes,
# indicator sizes) and swatch colours, which are product data rather than
# design decisions. Moving them to data-attribute rules is follow-up work.
INLINE_STYLE_BUDGET = 186
RAW_HEX_BUDGET = 0
IMPORTANT_BUDGET = 0
# Only the 11px functional floor remains, which is the allowed minimum.
UNDERSIZED_TEXT_BUDGET = 1


# Files exempt from the raw-hex rule: the token layer is where colour lives.
TOKEN_FILES = {"tokens.css"}

# Only @media preludes. A bare `max-width:` search also matches the CSS
# *property*, so a container capped at `max-width: 1320px` would count as a
# breakpoint it is not.
_MEDIA_RULE = re.compile(r"@media([^{]+)\{")
_MEDIA_W = re.compile(r"max-width:\s*(\d+)px")
_HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")
_INLINE_STYLE = re.compile(r'\sstyle="')
_FONT_REM = re.compile(r"font-size:\s*([\d.]+)rem")
_FONT_PX = re.compile(r"font-size:\s*([\d.]+)px")
# One flat `selector { declarations }` rule. Nested at-rule preludes cannot
# match, since neither half is allowed to cross a brace.
_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")


def _css_files():
    return sorted(_CSS.glob("*.css"))


def _read(p):
    return p.read_text(encoding="utf-8")


def _breakpoints():
    """max-width value -> set of files using it, counting @media preludes only."""
    by_value = {}
    for f in _css_files():
        widths = set()
        for prelude in _MEDIA_RULE.findall(_read(f)):
            widths |= {int(x) for x in _MEDIA_W.findall(prelude)}
        for v in widths:
            by_value.setdefault(v, set()).add(f.name)
    return by_value


class TestBreakpointDiscipline(unittest.TestCase):

    def test_breakpoint_count_within_budget(self):
        bps = _breakpoints()
        self.assertLessEqual(
            len(bps), BREAKPOINT_BUDGET,
            f"{len(bps)} distinct breakpoints (budget {BREAKPOINT_BUDGET}): {sorted(bps)}",
        )

    def test_orphan_breakpoints_within_budget(self):
        orphans = sorted(v for v, files in _breakpoints().items() if len(files) == 1)
        self.assertLessEqual(
            len(orphans), ORPHAN_BREAKPOINT_BUDGET,
            f"{len(orphans)} breakpoints appear in exactly one stylesheet, so that "
            f"one component reflows at a width nothing else does: {orphans}",
        )


class TestColourDiscipline(unittest.TestCase):

    def test_raw_hex_within_budget(self):
        total = sum(len(_HEX.findall(_read(f))) for f in _css_files() if f.name not in TOKEN_FILES)
        self.assertLessEqual(
            total, RAW_HEX_BUDGET,
            f"{total} raw hex values outside the token layer (budget {RAW_HEX_BUDGET})",
        )

    def test_no_new_important(self):
        """!important inside a prefers-reduced-motion block is the correct idiom
        and is exempt; everywhere else it is a specificity failure."""
        total = 0
        for f in _css_files():
            text = re.sub(r"@media[^{]*prefers-reduced-motion[^{]*\{(?:[^{}]|\{[^{}]*\})*\}", "", _read(f))
            total += text.count("!important")
        self.assertLessEqual(total, IMPORTANT_BUDGET, f"{total} !important (budget {IMPORTANT_BUDGET})")


class TestMotion(unittest.TestCase):

    def test_token_layer_carries_a_global_reduced_motion_reset(self):
        """One global reset in the token layer beats per-file duplication:
        every page loads tokens.css, so every page inherits it."""
        tokens = _CSS / "tokens.css"
        self.assertTrue(tokens.is_file(), "tokens.css is missing")
        block = re.search(
            r"@media[^{]*prefers-reduced-motion[^{]*\{(?:[^{}]|\{[^{}]*\})*\}", _read(tokens)
        )
        self.assertIsNotNone(block, "tokens.css declares no prefers-reduced-motion block")
        self.assertIn("*", block.group(0), "the reduced-motion block must be a global reset")

    def test_pages_on_the_token_layer_are_covered(self):
        """Any stylesheet whose page loads tokens.css is covered by the reset."""
        covered = set()
        for tpl in sorted(_TEMPLATES.glob("*.html")):
            sheets = re.findall(r'href="/static/css/([A-Za-z0-9_.-]+)"', _read(tpl))
            if "tokens.css" in sheets:
                covered |= set(sheets)
        self.assertIn("tokens.css", covered, "no template loads the token layer yet")

    def test_no_layout_property_transitions(self):
        """Animating width/height thrashes layout. Use transform instead."""
        offenders = []
        for f in _css_files():
            for i, line in enumerate(_read(f).splitlines(), 1):
                if re.search(r"transition:[^;}]*\b(width|height)\b", line):
                    offenders.append(f"{f.name}:{i}")
        # Progress now animates transform: scaleX, so none should remain.
        self.assertEqual(offenders, [], f"layout-property transitions: {offenders}")


class TestTypography(unittest.TestCase):

    def test_undersized_text_within_budget(self):
        sizes = set()
        for f in list(_css_files()) + list(_TEMPLATES.glob("*.html")):
            text = _read(f)
            sizes |= {round(float(v) * 16, 1) for v in _FONT_REM.findall(text)}
            sizes |= {float(v) for v in _FONT_PX.findall(text)}
        small = sorted(s for s in sizes if s < 12)
        self.assertLessEqual(
            len(small), UNDERSIZED_TEXT_BUDGET,
            f"{len(small)} distinct font sizes below 12px (budget {UNDERSIZED_TEXT_BUDGET}): {small}",
        )


class TestUploadTrays(unittest.TestCase):
    """Every upload tray in this product is a <button>, and a button shrink-wraps
    to its content. Four of the five shipped without `width: 100%` and rendered
    as ~164px chips adrift at the left of columns 512-892px wide, which read as
    stray decoration rather than a target. The declaration is the entire fix, so
    assert it here rather than rediscover it on a sixth tray.

    A tray is identified by what makes one: a dashed, clickable box. That way a
    tray added later is covered without anyone remembering to list it.
    """

    def _trays(self):
        """Every tray selector, with grouped selectors expanded.

        A grouped rule is one declaration block serving several trays — which is
        what sharing a component looks like. Counting rules instead of selectors
        would read that as a tray going missing.
        """
        found = []
        for f in _css_files():
            text = re.sub(r"/\*.*?\*/", "", _read(f), flags=re.S)
            for sel, body in _RULE.findall(text):
                if "dashed" in body and "cursor: pointer" in body:
                    for one in (s.strip() for s in sel.split(",")):
                        if one:
                            found.append((f.name, one, body, text))
        return found

    @staticmethod
    def _svg_rule_for(sel, text):
        """The declaration block of the `<sel> svg` rule, grouped or not."""
        for group, body in _RULE.findall(text):
            if f"{sel} svg" in (s.strip() for s in group.split(",")):
                return body
        return None

    def test_trays_are_discoverable(self):
        """Guards the heuristic itself: if it stops matching, the two tests
        below start passing vacuously."""
        # Four: storefront's modal tray, its free-search panel, the free-search
        # page and lens-recolour. It was five until the landing hero's tray went
        # — that page's Smart fit entry was a duplicate of the directory's own,
        # so the tray and its rules were deleted rather than left unreachable.
        self.assertGreaterEqual(
            len(self._trays()), 4,
            "the dashed+pointer heuristic no longer finds the upload trays, so "
            "the width and icon contracts below are asserting nothing",
        )

    def test_every_tray_fills_its_column(self):
        offenders = [
            f"{name} {sel}" for name, sel, body, _ in self._trays()
            if not re.search(r"width:\s*100%", body)
        ]
        self.assertEqual(
            offenders, [],
            f"upload trays that will shrink-wrap to their content: {offenders}",
        )

    def test_every_tray_icon_is_stroked(self):
        """The tray icons are stroke-only paths, and an SVG path paints its fill
        by default. Setting `color` alone leaves the glyph invisible while still
        reserving its box -- which is how the storefront tray shipped with a
        blank gap where the plus should be.

        Either half of the stack can satisfy this: the landing tray strokes via
        `stroke="currentColor"` in markup and tints through `color` in CSS, which
        is correct. So the contract is that the icon ends up stroked, not that
        any one layer does it.
        """
        templates = [_read(p) for p in sorted(_TEMPLATES.glob("*.html"))]
        offenders = []
        for name, sel, _, text in self._trays():
            css = self._svg_rule_for(sel, text)
            css_strokes = bool(css) and "fill:" in css and "stroke:" in css

            # The tray's icon is the <svg> opening immediately inside it.
            cls = sel.split(".")[-1].split()[0]
            icon = re.compile(
                r'class="[^"]*(?<![-\w])' + re.escape(cls) + r'(?![-\w])[^"]*"[^>]*>\s*(<svg[^>]*>)'
            )
            icons = [tag for html in templates for tag in icon.findall(html)]
            markup_strokes = bool(icons) and all("stroke=" in tag for tag in icons)

            if not (css_strokes or markup_strokes):
                offenders.append(f"{name} {sel}")
        self.assertEqual(
            offenders, [],
            f"tray icons that will not render: {offenders}",
        )


class TestInlineStyles(unittest.TestCase):

    def test_inline_style_count_within_budget(self):
        per_file = {f.name: len(_INLINE_STYLE.findall(_read(f))) for f in sorted(_TEMPLATES.glob("*.html"))}
        total = sum(per_file.values())
        self.assertLessEqual(
            total, INLINE_STYLE_BUDGET,
            f"{total} inline style attributes (budget {INLINE_STYLE_BUDGET}): {per_file}",
        )


if __name__ == "__main__":
    unittest.main()
