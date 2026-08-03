"""Ratchet budgets for the CSS layer.

Every number below is the measured state at the start of the redesign. They are
ceilings: lower them as work lands, never raise them. A test that fails because
a budget got smaller is the point.

Responsive breakage is one of this repo's chronic failure modes, and the cause
is visible in BREAKPOINT_BUDGET: 21 distinct max-width values across five
stylesheets, 13 of them used in exactly one file, so different parts of the same
page reflow at different widths.
"""
import re
import unittest
from pathlib import Path

_UI = Path(__file__).resolve().parent.parent
_CSS = _UI / "static" / "css"
_TEMPLATES = _UI / "templates"

# --- budgets: measured at redesign start. Lower these, never raise. ---
BREAKPOINT_BUDGET = 21          # target 6
ORPHAN_BREAKPOINT_BUDGET = 13   # breakpoints used in exactly one file; target 0
INLINE_STYLE_BUDGET = 237       # target 40
RAW_HEX_BUDGET = 181            # target 0 outside tokens.css
IMPORTANT_BUDGET = 3            # target 0
UNDERSIZED_TEXT_BUDGET = 12     # distinct font sizes below 12px; target 0
REDUCED_MOTION_FLOOR = 1        # stylesheets declaring it; target = all of them

# Files exempt from the raw-hex rule: the token layer is where colour lives.
TOKEN_FILES = {"tokens.css"}

_MEDIA_W = re.compile(r"max-width:\s*(\d+)px")
_HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")
_INLINE_STYLE = re.compile(r'\sstyle="')
_FONT_REM = re.compile(r"font-size:\s*([\d.]+)rem")
_FONT_PX = re.compile(r"font-size:\s*([\d.]+)px")


def _css_files():
    return sorted(_CSS.glob("*.css"))


def _read(p):
    return p.read_text(encoding="utf-8")


def _breakpoints():
    """max-width value -> set of files using it."""
    by_value = {}
    for f in _css_files():
        for v in {int(x) for x in _MEDIA_W.findall(_read(f))}:
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
        total = sum(_read(f).count("!important") for f in _css_files())
        self.assertLessEqual(total, IMPORTANT_BUDGET, f"{total} !important (budget {IMPORTANT_BUDGET})")


class TestMotion(unittest.TestCase):

    def test_reduced_motion_coverage_does_not_regress(self):
        declaring = [f.name for f in _css_files() if "prefers-reduced-motion" in _read(f)]
        self.assertGreaterEqual(
            len(declaring), REDUCED_MOTION_FLOOR,
            f"only {declaring} declare prefers-reduced-motion",
        )

    def test_no_layout_property_transitions(self):
        """Animating width/height thrashes layout. Use transform instead."""
        offenders = []
        for f in _css_files():
            for i, line in enumerate(_read(f).splitlines(), 1):
                if re.search(r"transition:[^;}]*\b(width|height)\b", line):
                    offenders.append(f"{f.name}:{i}")
        # Two exist today (progress bars); this asserts no new ones appear.
        self.assertLessEqual(len(offenders), 2, f"layout-property transitions: {offenders}")


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
