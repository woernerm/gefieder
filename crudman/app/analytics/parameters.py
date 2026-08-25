"""The ``${name}`` placeholders a query's SQL and a chart's options share.

One syntax for both sides, so an author learns it once:

* ``${name}`` in SQL is **bound**. It is rewritten to psycopg's ``%(name)s`` and the
  value travels beside the statement, never inside it.
* ``${name:format}`` in SQL is **interpolated** -- the value becomes part of the
  statement text. That is what binding cannot do: an identifier, an ``IN`` list, and
  later the time macros. Every format quotes or validates what it emits; see FORMATS.
* ``${name}`` in a chart's options names a **placeholder**, resolved to the column a panel
  bound it to. Charts are written without column names so one chart serves many queries.

Interpolation is a deliberate widening of what a stored query can reach, taken on the
same terms Grafana takes it: the analytics role may only read. Read-only stops writes,
not reads, so an interpolated value can reach any table that role sees. What keeps that
bounded is the rule that parameter values are *stored*, never taken from the query
string -- see analytics.views.
"""

import re

from psycopg import sql

PLACEHOLDER = re.compile(r"\$\{(?P<name>[a-z_][a-z0-9_]*)(?::(?P<format>[a-z]+))?\}")
"""``${name}`` or ``${name:format}``. Names are lowercase identifiers, as SQL is."""

PLACEHOLDER_TOKEN = re.compile(r"^\$\{(?P<name>[a-z_][a-z0-9_]*)(?P<list>\[\])?\}$")
"""A chart placeholder, matched only as a whole string so a label or formatter is never rewritten."""

_MARKER = "\x00"
"""Stands in for a bound placeholder while the percent signs around it are escaped.

A NUL cannot occur in the statement text PostgreSQL accepts, so nothing an author writes
can be mistaken for one.
"""

_MARKED = re.compile(_MARKER + r"([a-z_][a-z0-9_]*)" + _MARKER)


def _identifier(value):
    """One or more dot-separated identifiers, quoted by psycopg rather than by hand."""
    parts = str(value).split(".")
    return sql.SQL(".").join(sql.Identifier(part) for part in parts).as_string(None)


def _literal(value):
    """A SQL literal, quoted by psycopg -- the same escaping a bound value would get."""
    return sql.Literal(value).as_string(None)


def _csv(value):
    """A comma-separated list of literals, for ``IN (${tenants:csv})``."""
    values = value if isinstance(value, (list, tuple)) else [value]
    return ", ".join(_literal(item) for item in values)


def _raw(value):
    """The value as it stands. The one format that validates nothing."""
    return str(value)


FORMATS = {
    "identifier": _identifier,
    "sqlstring": _literal,
    "doublequote": _identifier,
    "csv": _csv,
    "raw": _raw,
}
"""How each ``${name:format}`` turns a value into statement text.

``doublequote`` is Grafana's name for what PostgreSQL calls quoting an identifier, so it
maps onto the same function; ``sqlstring`` is its name for a quoted literal. ``raw``
validates nothing and is the one to reach for last.
"""


class ParameterError(Exception):
    """A placeholder has no value, or names a format that does not exist."""


def names(text):
    """Every placeholder name appearing in ``text``, in order of first appearance.

    Args:
        text: SQL or any string carrying ``${name}`` placeholders.

    Returns:
        The distinct names, ordered as they occur.
    """
    seen = {}
    for match in PLACEHOLDER.finditer(text or ""):
        seen.setdefault(match.group("name"), None)
    return list(seen)


def bind(sql_text, values):
    """Rewrite a query's placeholders for execution.

    Args:
        sql_text: The stored statement, carrying ``${name}`` placeholders.
        values: The value for every placeholder in it.

    Returns:
        A tuple of the statement psycopg should run and the parameters to bind to it.
        Bound placeholders become ``%(name)s`` and appear in the parameters; formatted
        ones are substituted into the text and do not.

    Raises:
        ParameterError: A placeholder has no value, or names an unknown format.
    """
    bound = {}

    def replace(match):
        name, fmt = match.group("name"), match.group("format")
        if name not in values:
            raise ParameterError(f"No value for ${{{name}}}.")
        if fmt is None:
            bound[name] = values[name]
            return f"{_MARKER}{name}{_MARKER}"
        if fmt not in FORMATS:
            raise ParameterError(
                f"Unknown format {fmt!r}; use one of {', '.join(sorted(FORMATS))}."
            )
        return FORMATS[fmt](values[name])

    statement = PLACEHOLDER.sub(replace, sql_text or "")

    # Once anything is bound, psycopg reads the statement for placeholders of its own and
    # rejects every other percent sign -- a LIKE pattern's, or one inside an interpolated
    # value. Doubling them all and only then writing the real placeholders is what lets a
    # query hold both. With nothing bound psycopg never looks, so the text stays as it is.
    if bound:
        statement = statement.replace("%", "%%")
        statement = _MARKED.sub(r"%(\1)s", statement)

    return statement, bound


def placeholders(options):
    """Every placeholder name a chart's options and transforms refer to.

    Args:
        options: Any JSON-shaped structure from a chart.

    Returns:
        A dict of placeholder name to whether it was written as a list (``${name[]}``). A name
        used both ways counts as a list, that being the wider of the two.
    """
    found = {}

    def walk(node):
        if isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
        elif isinstance(node, str):
            match = PLACEHOLDER_TOKEN.match(node)
            if match:
                name = match.group("name")
                found[name] = bool(match.group("list")) or found.get(name, False)

    walk(options)
    return found


def resolve(node, bindings):
    """Replace a chart's placeholders with the columns a panel bound them to.

    Only a string that is *entirely* a placeholder is replaced, so a formatter or a label
    mentioning ``${...}`` is left as the author wrote it.

    Args:
        node: The structure to resolve; it is not modified.
        bindings: Placeholder name to column name, or to a list of them for a list placeholder.

    Returns:
        A copy with every bound placeholder replaced. An unbound placeholder is left in place, which is
        what makes the resulting chart fail visibly rather than silently plot nothing.
    """
    if isinstance(node, dict):
        return {key: resolve(value, bindings) for key, value in node.items()}
    if isinstance(node, list):
        return [resolve(value, bindings) for value in node]
    if isinstance(node, str):
        match = PLACEHOLDER_TOKEN.match(node)
        if match and match.group("name") in bindings:
            return bindings[match.group("name")]
    return node


def statement_count(sql_text):
    """How many statements a query text holds.

    A plain count of semicolons is wrong twice over: one inside a string literal is data,
    and one inside a comment is nothing at all. Both appear in ordinary SQL -- a literal
    separator, a commented-out clause -- and rejecting them would refuse a query that is
    perfectly sound.

    Args:
        sql_text: The statement text, before any placeholder substitution.

    Returns:
        The number of statements, counting a trailing semicolon as ending the last one
        rather than beginning another.
    """
    text = sql_text or ""
    statements = 1
    index = 0
    length = len(text)

    while index < length:
        char = text[index]

        if char == "'":
            # A doubled quote inside a literal is an escaped one, and the scan below
            # steps over it naturally: the closing quote it finds is the second of the
            # pair, and the next iteration re-enters the literal.
            index = text.find("'", index + 1)
            if index < 0:
                break
        elif char == '"':
            index = text.find('"', index + 1)
            if index < 0:
                break
        elif text.startswith("--", index):
            end = text.find("\n", index)
            index = length if end < 0 else end
        elif text.startswith("/*", index):
            end = text.find("*/", index + 2)
            index = length if end < 0 else end + 1
        elif char == ";" and text[index + 1:].strip():
            statements += 1

        index += 1

    return statements
