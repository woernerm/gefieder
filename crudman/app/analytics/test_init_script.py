"""Guarding the one thing about analytics.init.js that a Python test suite can still catch.

Unfold emits UNFOLD["SCRIPTS"] into the <head> without ``defer``, so the script runs
before the document has a body. Reaching for ``document.body`` -- or for
``document.querySelector("html")``, which is equally absent that early -- at the top level
throws, and the exception takes the whole script down with it: the cards still arrive over
HTMX but no chart is ever drawn, with nothing failing loudly anywhere.

That bug is invisible to every other test here. The Django tests never execute
JavaScript, the integration suite asserts the fragment's HTML rather than what the browser
does with it, and the image carries no JavaScript runtime to run the file in. What is left
is reading the source, which is enough because the mistake has a stable shape: a DOM
lookup that is not inside a function.
"""

import re
from pathlib import Path

from django.test import SimpleTestCase

SCRIPT = Path(__file__).resolve().parent / "static" / "analytics" / "analytics.init.js"

# Every expression that needs a parsed document. documentElement is deliberately absent:
# it exists as soon as the parser has seen <html>, so it is safe at head time and is what
# the script uses instead.
NEEDS_THE_DOCUMENT = (
    "document.body",
    'document.querySelector("html")',
    "document.querySelector('html')",
)


def top_level_lines(source):
    """The lines of an IIFE-wrapped script that run as soon as it is parsed.

    Brace depth stands in for scope: the file is one ``(() => { ... })()``, so its own
    body sits at depth one and anything deeper is inside a function that runs later.

    Args:
        source: The script's text.

    Returns:
        Pairs of line number and text for the lines that execute at parse time.
    """
    lines = []
    depth = 0
    for number, line in enumerate(source.splitlines(), start=1):
        code = re.sub(r"//.*", "", line)
        if depth <= 1:
            lines.append((number, line))
        depth += code.count("{") - code.count("}")
    return lines


class InitScriptTests(SimpleTestCase):
    def test_the_script_shall_not_touch_the_document_before_it_is_parsed(self):
        """The script is loaded in the head, so the body does not exist yet."""
        offenders = [
            (number, expression, line.strip())
            for number, line in top_level_lines(SCRIPT.read_text())
            for expression in NEEDS_THE_DOCUMENT
            if expression in re.sub(r"//.*", "", line)
        ]

        self.assertEqual(
            offenders,
            [],
            "analytics.init.js runs in the <head>, where these do not exist yet. Move the "
            "call inside the DOMContentLoaded handler, or use document.documentElement:\n"
            + "\n".join(f"  line {n}: {expr} in {text}" for n, expr, text in offenders),
        )

    def test_the_script_shall_wire_itself_up_once_the_document_is_parsed(self):
        """The deferred wiring is the fix; without it nothing would render at all."""
        source = SCRIPT.read_text()
        self.assertIn("DOMContentLoaded", source)
        # A script injected after parsing never sees the event, so the ready state has to
        # be checked rather than the listener simply registered.
        self.assertIn("document.readyState", source)

    def test_the_theme_shall_not_hand_every_chart_a_pair_of_axes(self):
        """A pie has no axes; ECharts draws empty default ones if the options mention them.

        The theme is merged over what the server sends, so an xAxis/yAxis sitting in it
        unconditionally reaches charts that never asked for one. They belong under a key
        applyTheme copies onto the axes a chart actually declares.
        """
        source = SCRIPT.read_text()
        theme = source[source.index("const themeOptions"):source.index("const merge")]

        for axis in ("xAxis:", "yAxis:"):
            self.assertNotIn(
                axis,
                theme,
                f"themeOptions() must not define {axis.rstrip(':')}: every chart is "
                "merged over it, so a pie would be given empty axes. Put the styling "
                "under the 'axis' key, which applyTheme copies onto declared axes only.",
            )

    def test_each_chart_shall_be_told_when_its_own_box_changes_size(self):
        """The sidebar toggles by changing classes, which fires no window resize event.

        ECharts measures its container once, at init, so a chart beside a collapsing
        sidebar keeps its old width and sits off-centre. Listening on the window alone
        does not see it; the element itself has to be observed.
        """
        source = SCRIPT.read_text()
        self.assertIn("ResizeObserver", source)
        self.assertIn("observer.observe(element)", source)
        # An observer left running after its chart is disposed keeps the element alive.
        self.assertIn("disconnect()", source)

    def test_the_guard_shall_notice_the_mistake_it_exists_for(self):
        """The check is only worth having if it fails on the code that caused the bug."""
        regressed = (
            "(() => {\n"
            '  document.body.addEventListener("htmx:afterSwap", () => {});\n'
            "})();\n"
        )
        found = [
            expression
            for _, line in top_level_lines(regressed)
            for expression in NEEDS_THE_DOCUMENT
            if expression in line
        ]
        self.assertEqual(found, ["document.body"])
