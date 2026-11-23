"""What every template in this project has to hold true, whatever it renders.

One test, because the failure it catches is silent: a template that renders its own source
looks like a template that renders nothing unusual until somebody reads the page.
"""

from pathlib import Path

from django.conf import settings
from django.test import TestCase

TEMPLATES = sorted(
    path
    for app in Path(settings.BASE_DIR).iterdir()
    if app.is_dir()
    for path in (app / "templates").rglob("*.html")
)
"""Every template this project ships, found where Django's app loader looks for them."""


class TemplateCommentTests(TestCase):
    def test_no_comment_is_left_open_at_the_end_of_a_line(self):
        """A "{# ... #}" comment cannot span lines, so an unclosed one is shown to users.

        Django's lexer matches the whole comment in one line for parsing speed, and a "{#"
        it never closes on that line is not a comment at all: it is text, and it renders.
        Nothing warns about it -- the page simply grows a paragraph explaining itself to
        the reader. So each line closes its own comment, and a block comment is written as
        several of them rather than as one that wraps.
        """
        unclosed = [
            f"{path.relative_to(settings.BASE_DIR)}:{number}"
            for path in TEMPLATES
            for number, line in enumerate(path.read_text().splitlines(), 1)
            if "{#" in line and "#}" not in line
        ]

        self.assertEqual(
            unclosed,
            [],
            "these comments are not closed on their own line, so they render as text; "
            "write a block comment as one {# ... #} per line",
        )

    def test_the_templates_were_actually_found(self):
        """A guard that finds nothing guards nothing."""
        self.assertGreater(len(TEMPLATES), 1)
