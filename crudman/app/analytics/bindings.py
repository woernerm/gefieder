"""Proposing which column each of a chart's placeholders should read.

A chart names placeholders, a query returns columns, and a panel says which is which.
Asking an author to fill that in from nothing would make the reuse the split buys feel
like a cost, so the form arrives already filled -- and every placeholder stays editable,
because a proposal is only a guess.

The guess is name similarity and nothing else: an author who wrote ``${total}`` against a
query returning ``total_effort`` meant them to meet. Which pairing is best is a question
about the whole set rather than one placeholder at a time, since taking the closest
column for one can strand another, so the pairing with the highest total similarity is
chosen outright.
"""

from difflib import SequenceMatcher

from scipy.optimize import linear_sum_assignment


def propose(placeholders, columns):
    """Suggest a column for every placeholder a chart declares.

    Args:
        placeholders: Placeholder name to whether it takes a list, as
            ``parameters.placeholders`` returns.
        columns: The available columns, each ``{"name", ...}``, in query order.

    Returns:
        A dict of placeholder name to a column name, or to a list of them for a list
        placeholder, which takes every column no scalar one claimed -- that being what
        ``${measures[]}`` is for. A placeholder nothing resembles at all is left out
        rather than filled with a wrong answer.
    """
    names = [column["name"] for column in columns]
    scalar = [name for name, is_list in placeholders.items() if not is_list]
    if not names or not scalar:
        return {}

    def score(placeholder, column):
        return SequenceMatcher(None, placeholder.lower(), column.lower()).ratio()

    # Maximising similarity is minimising its complement, which is what the solver does.
    rows, taken = linear_sum_assignment(
        [[1 - score(p, c) for c in names] for p in scalar]
    )
    proposal = {
        scalar[row]: names[column]
        for row, column in zip(rows, taken)
        if score(scalar[row], names[column])
    }

    spare = [name for name in names if name not in proposal.values()]
    for placeholder, is_list in placeholders.items():
        if is_list and spare:
            proposal[placeholder] = list(spare)

    return proposal
