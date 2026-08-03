"""Ratchet budgets for the CSS layer.

Every number below is the measured state at the start of the redesign. They are
ceilings: lower them as work lands, never raise them. A test that fails because
a budget got smaller is the point.

Responsive breakage is one of this repo's chronic failure modes. The measured
cause is narrow but real: two orphan breakpoints. 540px lives only in
free-search.css and 768px only in common.css, so the results grid reflows at a
width nothing else on the page uses.
"""
import re
import unittest
from pathlib import Path

_UI = Path(__file__).resolve().parent.parent
_CSS = _UI / "static" / "css"
_TEMPLATES = _UI / "templates"

# --- budgets. Lower these, never raise. ---
# Mid-migration: the old set (380/540/600/768/900/1024) coexists with the new
# one (560/900/1280) until the last page moves off common.css. Target is 3.
BREAKPOINT_BUDGET = 7
ORPHAN_BREAKPOINT_BUDGET = 2    # 540 in free-search only, 768 in common only; target 0
INLINE_STYLE_BUDGET = 237       # target 40
RAW_HEX_BUDGET = 181            # target 0 outside tokens.css
IMPORTANT_BUDGET = 3            # target 0
UNDERSIZED_TEXT_BUDGET = 12     # distinct font sizes below 12px; target 0


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
        """One global reset in the token layer beats per-file duplication: every
        page that loads tokens.css inherits it. Pages still on common.css have
        no coverage at all and gain it when they move over."""
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
