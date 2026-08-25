"""Proposing which column each of a chart's placeholders should read.

A chart names placeholders, a query returns columns, and a panel says which is which. Asking an
author to fill that in from nothing would make the reuse the split buys feel like a cost,
so the form arrives already filled: the proposal below is what the panel admin offers,
and every placeholder stays editable because a proposal is a guess.

Two things are scored. The name, because an author who wrote ``${total}`` and a query
returning ``total_effort`` meant them to meet. And the kind, because the option object
already says where a placeholder sits -- a placeholder used as ``encode.x`` against a category axis
wants a column of labels, one used as ``encode.y`` wants a number -- so a proposal that
puts a timestamp on a value axis can be discounted without anyone declaring anything.
"""

from difflib import SequenceMatcher

from . import parameters

EXACT = 100.0
SUBSTRING = 60.0
SIMILARITY = 50.0
"""What a name match is worth: the whole score, most of it, or a share of it."""

KIND_PENALTY = 0.3
"""Multiplier for a column whose kind is not the one the placeholder's position implies."""

KIND_MATCH = 5.0
"""Added when the kind agrees, so a placeholder whose name resembles nothing can still be
proposed a column of the right sort -- ``${v}`` and a column called ``n`` share no
letters, and leaving the field empty would be the less useful answer."""

TAKEN_PENALTY = 0.5
"""Two placeholders reading one column stays possible -- a table's rows and columns could not
both be filled otherwise when only one column suits -- but any untaken column of the
right kind is preferred to it."""


def _name_score(placeholder, column):
    """How much the two names look like each other, from 0 to EXACT."""
    placeholder, column = placeholder.lower(), column.lower()
    if placeholder == column:
        return EXACT
    if placeholder in column or column in placeholder:
        return SUBSTRING
    return SequenceMatcher(None, placeholder, column).ratio() * SIMILARITY


def expected_kinds(options):
    """Which kind of column each placeholder's position in the option object implies.

    Args:
        options: The chart's option object, as stored.

    Returns:
        A dict of placeholder name to "number", "category" or None when the position says
        nothing. Only ``encode`` is read: it is where a series states what a dimension is
        for, so it is the one place the chart already carries the answer.
    """
    kinds = {}

    def walk(node):
        if isinstance(node, dict):
            encode = node.get("encode")
            if isinstance(encode, dict):
                # A matrix series addresses cells by two headers, so both of its axes
                # carry labels; on a grid, y is the measure. The same channel therefore
                # means different things, and the series says which.
                matrix = node.get("coordinateSystem") == "matrix"
                for channel, placeholder in encode.items():
                    for name in _placeholder_names(placeholder):
                        kinds.setdefault(name, _channel_kind(channel, matrix))
            for key, value in node.items():
                if key != "encode":
                    walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(options)
    return {name: kind for name, kind in kinds.items() if kind}


def _channel_kind(channel, matrix=False):
    """The kind an ``encode`` channel implies, or None when it implies nothing.

    Args:
        channel: The encode channel, such as "x" or "value".
        matrix: Whether the series draws on a matrix, where x and y are both headers.
    """
    if matrix:
        return "number" if channel == "value" else "category"
    if channel in ("y", "value", "radius"):
        return "number"
    if channel in ("x", "itemName", "seriesName", "angle"):
        return "category"
    return None


def _placeholder_names(value):
    """The placeholder names inside one ``encode`` channel, which may hold a list."""
    values = value if isinstance(value, list) else [value]
    names = []
    for entry in values:
        if isinstance(entry, str):
            match = parameters.PLACEHOLDER_TOKEN.match(entry)
            if match:
                names.append(match.group("name"))
    return names


def propose(placeholders, columns, options=None):
    """Suggest a column for every placeholder a chart declares.

    Args:
        placeholders: Placeholder name to whether it takes a list, as ``parameters.placeholders`` returns.
        columns: The available columns, each ``{"name", "kind", ...}``, in query order.
        options: The chart's option object, read only to tell a value axis from a
            category one.

    Returns:
        A dict of placeholder name to a column name, or to a list of them for a list placeholder.
        Assignment is greedy on the best score, so the clearest match is settled first
        and cannot be taken by a weaker one. A placeholder no column suits is left out rather
        than filled with a wrong answer.
    """
    kinds = expected_kinds(options or {})
    scores = []
    for placeholder in placeholders:
        for column in columns:
            score = _name_score(placeholder, column["name"])
            if kinds.get(placeholder):
                if kinds[placeholder] == column.get("kind"):
                    score += KIND_MATCH
                else:
                    score *= KIND_PENALTY
            scores.append((score, placeholder, column["name"]))

    scores.sort(key=lambda entry: (-entry[0], entry[1], entry[2]))

    proposal = {}
    taken = set()
    deferred = []

    def wants(placeholder):
        """Whether this placeholder can still take a column in the first round."""
        return placeholders[placeholder] or placeholder not in proposal

    def give(placeholder, column):
        if placeholders[placeholder]:
            proposal.setdefault(placeholder, [])
            if column not in proposal[placeholder]:
                proposal[placeholder].append(column)
        else:
            proposal[placeholder] = column
        taken.add(column)

    # First round: every placeholder gets a column of its own, best match first. A column
    # already spoken for is not assigned here but held back, so a placeholder that could have
    # had an unused column is not handed a shared one just because it scored higher.
    for score, placeholder, column in scores:
        if score <= 0 or not wants(placeholder):
            continue
        if column in taken:
            deferred.append((score * TAKEN_PENALTY, placeholder, column))
            continue
        # A list placeholder collects only the kind it asked for -- a stacked bar wants every
        # measure, not the label column beside them.
        if placeholders[placeholder] and kinds.get(placeholder) and kinds[placeholder] != _kind_of(column, columns):
            continue
        give(placeholder, column)

    # Second round: whatever is still empty may share a column, since a table's rows and
    # columns cannot both be filled otherwise when only one column suits.
    deferred.sort(key=lambda entry: (-entry[0], entry[1], entry[2]))
    for score, placeholder, column in deferred:
        if score <= 0 or placeholder in proposal:
            continue
        give(placeholder, column)

    return proposal


def _kind_of(name, columns):
    """The kind of the named column, or None when it is not among them."""
    for column in columns:
        if column["name"] == name:
            return column.get("kind")
    return None
