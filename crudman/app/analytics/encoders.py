"""Keeping the stored JSON readable in the admin form.

An ECharts option object is nested and easily fifty lines long, and a panel is written by
pasting one in and editing it. Django's JSON form field renders its value with
``json.dumps(value, cls=self.encoder)`` and no indent, so whatever an author pasted comes
back as a single unreadable line the moment the page reloads.

Indentation cannot be stored: jsonb keeps the value, not its text. So the form indents the
value on the way out instead, which makes the field readable however the row was written
-- pasted in, seeded by analytics.examples, or inserted by something else entirely.
"""

import json


class PrettyJSONEncoder(json.JSONEncoder):
    """A JSON encoder that indents, for the admin form to render values with.

    Passed as a field's ``encoder``, which is what Django hands to ``json.dumps`` both
    when preparing the form value and when writing to the database. Postgres normalises
    the whitespace away on the way in, so the indentation only ever shows in the form.
    """

    def __init__(self, *args, **kwargs):
        kwargs["indent"] = 2
        # Sorting would reorder an author's option object on every save; ECharts does not
        # care about key order but a person reading the field does.
        kwargs["sort_keys"] = False
        super().__init__(*args, **kwargs)
