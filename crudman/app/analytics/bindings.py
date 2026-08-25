"""Proposing which column each of a chart's slots should read.

A chart names slots, a query returns columns, and a panel says which is which. Asking an
author to fill that in from nothing would make the reuse the split buys feel like a cost,
so the form arrives already filled: the proposal below is what the panel admin offers,
and every slot stays editable because a proposal is a guess.

Two things are scored. The name, because an author who wrote ``${total}`` and a query
returning ``total_effort`` meant them to meet. And the kind, because the option object
already says where a slot sits -- a slot used as ``encode.x`` against a category axis
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
"""Multiplier for a column whose kind is not the one the slot's position implies."""

KIND_MATCH = 5.0
"""Added when the kind agrees, so a slot whose name resembles nothing can still be
proposed a column of the right sort -- ``${v}`` and a column called ``n`` share no
letters, and leaving the field empty would be the less useful answer."""

TAKEN_PENALTY = 0.5
"""Two slots reading one column stays possible -- a table's rows and columns could not
both be filled otherwise when only one column suits -- but any untaken column of the
right kind is preferred to it."""


def _name_score(slot, column):
    """How much the two names look like each other, from 0 to EXACT."""
    slot, column = slot.lower(), column.lower()
    if slot == column:
        return EXACT
    if slot in column or column in slot:
        return SUBSTRING
    return SequenceMatcher(None, slot, column).ratio() * SIMILARITY


def expected_kinds(options):
    """Which kind of column each slot's position in the option object implies.

    Args:
        options: The chart's option object, as stored.

    Returns:
        A dict of slot name to "number", "category" or None when the position says
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
                for channel, slot in encode.items():
                    for name in _slot_names(slot):
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


def _slot_names(value):
    """The slot names inside one ``encode`` channel, which may hold a list."""
    values = value if isinstance(value, list) else [value]
    names = []
    for entry in values:
        if isinstance(entry, str):
            match = parameters.SLOT.match(entry)
            if match:
                names.append(match.group("name"))
    return names


def propose(slots, columns, options=None):
    """Suggest a column for every slot a chart declares.

    Args:
        slots: Slot name to whether it takes a list, as ``parameters.slots`` returns.
        columns: The available columns, each ``{"name", "kind", ...}``, in query order.
        options: The chart's option object, read only to tell a value axis from a
            category one.

    Returns:
        A dict of slot name to a column name, or to a list of them for a list slot.
        Assignment is greedy on the best score, so the clearest match is settled first
        and cannot be taken by a weaker one. A slot no column suits is left out rather
        than filled with a wrong answer.
    """
    kinds = expected_kinds(options or {})
    scores = []
    for slot in slots:
        for column in columns:
            score = _name_score(slot, column["name"])
            if kinds.get(slot):
                if kinds[slot] == column.get("kind"):
                    score += KIND_MATCH
                else:
                    score *= KIND_PENALTY
            scores.append((score, slot, column["name"]))

    scores.sort(key=lambda entry: (-entry[0], entry[1], entry[2]))

    proposal = {}
    taken = set()
    deferred = []

    def wants(slot):
        """Whether this slot can still take a column in the first round."""
        return slots[slot] or slot not in proposal

    def give(slot, column):
        if slots[slot]:
            proposal.setdefault(slot, [])
            if column not in proposal[slot]:
                proposal[slot].append(column)
        else:
            proposal[slot] = column
        taken.add(column)

    # First round: every slot gets a column of its own, best match first. A column
    # already spoken for is not assigned here but held back, so a slot that could have
    # had an unused column is not handed a shared one just because it scored higher.
    for score, slot, column in scores:
        if score <= 0 or not wants(slot):
            continue
        if column in taken:
            deferred.append((score * TAKEN_PENALTY, slot, column))
            continue
        # A list slot collects only the kind it asked for -- a stacked bar wants every
        # measure, not the label column beside them.
        if slots[slot] and kinds.get(slot) and kinds[slot] != _kind_of(column, columns):
            continue
        give(slot, column)

    # Second round: whatever is still empty may share a column, since a table's rows and
    # columns cannot both be filled otherwise when only one column suits.
    deferred.sort(key=lambda entry: (-entry[0], entry[1], entry[2]))
    for score, slot, column in deferred:
        if score <= 0 or slot in proposal:
            continue
        give(slot, column)

    return proposal


def _kind_of(name, columns):
    """The kind of the named column, or None when it is not among them."""
    for column in columns:
        if column["name"] == name:
            return column.get("kind")
    return None
