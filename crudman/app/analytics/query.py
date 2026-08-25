"""Running a panel's SQL on the analytics connection.

Two guards, both the database's rather than this module's:

* the connection authenticates as the analytics role, which holds no write grant on any
  schema it can read -- the same role and therefore the same results as Grafana, and
* every statement runs inside a read-only transaction with a statement timeout, so a
  panel can neither write nor occupy a backend indefinitely.

Both, because neither is sufficient alone. A read-only transaction can be lifted from
inside by a statement beginning ``SET TRANSACTION READ WRITE``, which the database
accepts; what stops the write that follows is the role holding no grant for it. The
statement is run as a single prepared statement to keep that escape out of reach in the
first place -- see ``run``.

Inspecting the SQL text for forbidden words is deliberately absent: a blocklist is
defeated by a comment or a cast, while the role's own grants are not.

A ``${name:format}`` placeholder puts its value into the statement text rather than
beside it, so what a stored query can reach is bounded by the role's read grants alone --
see analytics.parameters. Nothing widens that further: parameter values are stored, never
read from the query string.
"""

import hashlib
import json
import os

from django.core.cache import InvalidCacheBackendError, caches
from django.db import connections

from . import parameters

ANALYTICS_CONNECTION = "analytics"
"""The DATABASES alias that authenticates as the analytics (grafana) role."""

STATEMENT_TIMEOUT = os.environ.get("PANEL_STATEMENT_TIMEOUT", "30s")
"""How long a single panel query may run before the database cancels it."""

RESULT_CACHE = "analytics"
"""The cache alias the shared results live in; see CACHES in settings.py."""

RESULT_TTL = int(os.environ.get("PANEL_RESULT_TTL", "15"))
"""Seconds a result is reused for.

Short on purpose. This is not a cache in the sense of keeping data warm -- it exists to
collapse the burst of fetches one page load makes. A dashboard draws each panel with its
own request, so two panels built on one query would otherwise run it twice within the
same second. Anything longer would start serving a dashboard that disagrees with the
database, which is the opposite of what a metric is for.
"""


class PanelQueryError(Exception):
    """A panel's query could not be run, with a message fit to show the user."""


NUMERIC_OIDS = frozenset({20, 21, 23, 26, 700, 701, 1700})
"""int8, int2, int4, oid, float4, float8, numeric."""

TIME_OIDS = frozenset({1082, 1083, 1114, 1184, 1266})
"""date, time, timestamp, timestamptz, timetz."""


def kind_of(type_oid):
    """Which of the three kinds a column's type counts as.

    Args:
        type_oid: The PostgreSQL type OID psycopg reported.

    Returns:
        "number", "time" or "category" -- the only distinction the binding form needs to
        tell a value axis from a category one.
    """
    if type_oid in NUMERIC_OIDS:
        return "number"
    if type_oid in TIME_OIDS:
        return "time"
    return "category"


def describe(sql_text, values):
    """The columns a query would return, without running it.

    Wrapping the statement in ``SELECT * FROM (...) t WHERE false`` makes PostgreSQL
    plan it and report the result columns while folding the body away as a one-time
    false filter, so nothing is read and nothing is counted against the timeout. That is
    what makes a signature cheap enough to refresh on every save.

    Args:
        sql_text: The stored statement, carrying ``${name}`` placeholders.
        values: The value for every placeholder in it.

    Returns:
        A list of ``{"name", "type_oid", "kind"}``, one per result column, in order.

    Raises:
        PanelQueryError: The database rejected the statement.
    """
    statement, bound = parameters.bind(sql_text, values)
    connection = connections[ANALYTICS_CONNECTION]
    try:
        with connection.cursor() as cursor:
            cursor.execute("BEGIN READ ONLY")
            try:
                cursor.execute("SET LOCAL statement_timeout = %s", [STATEMENT_TIMEOUT])
                # The newlines matter as much as the wrapper: a trailing "-- comment"
                # would otherwise swallow the closing bracket and the false filter with
                # it. The semicolon goes for the same reason, having no place mid-query.
                cursor.execute(
                    "SELECT * FROM (\n"
                    f"{statement.strip().rstrip(';')}\n"
                    ") _gf_probe WHERE false",
                    bound or None,
                )
                columns = [
                    {
                        "name": column.name,
                        "type_oid": column.type_code,
                        "kind": kind_of(column.type_code),
                    }
                    for column in cursor.description or []
                ]
            finally:
                cursor.execute("ROLLBACK")
    except Exception as error:
        raise PanelQueryError(str(error).strip().splitlines()[0]) from error

    return columns


def _key(sql_text, values):
    """A cache key for one statement and one set of values.

    The statement text is part of it, so editing a query takes effect at once rather
    than after the entries for the old one expire.

    Args:
        sql_text: The stored statement.
        values: The values it will be run with.

    Returns:
        A short, stable string.
    """
    material = json.dumps([sql_text, values], sort_keys=True, default=str)
    return "analytics.result." + hashlib.sha256(material.encode()).hexdigest()


def run_shared(sql_text, values):
    """Run a query, or reuse a result another panel just fetched.

    This is what makes a query defined once cost one execution: the panels of a dashboard
    that read the same query with the same values share the rows between them instead of
    each asking the database for its own copy.

    How completely they share depends on the cache the deployment configures. The default
    is per-process, so with more than one gunicorn worker some panels still run the query
    themselves; pointing the ``analytics`` cache alias at a shared backend makes it exact.
    Correctness does not depend on it either way -- a miss simply runs the query.

    Args:
        sql_text: The stored statement, carrying ``${name}`` placeholders.
        values: The value for every placeholder in it.

    Returns:
        A tuple of the column names and the list of row tuples.

    Raises:
        PanelQueryError: The database rejected or cancelled the statement.
    """
    if RESULT_TTL <= 0:
        return run(sql_text, values)

    try:
        cache = caches[RESULT_CACHE]
    except InvalidCacheBackendError:
        # Sharing is an optimisation, so a deployment that has not declared the alias
        # gets the queries run rather than an error page.
        return run(sql_text, values)

    key = _key(sql_text, values)

    shared = cache.get(key)
    if shared is not None:
        return shared

    result = run(sql_text, values)
    cache.set(key, result, RESULT_TTL)
    return result


def run(sql_text, values):
    """Execute a query and return its columns and rows.

    Args:
        sql_text: The stored statement, carrying ``${name}`` placeholders.
        values: The value for every placeholder in it.

    Returns:
        A tuple of the column names and the list of row tuples.

    Raises:
        PanelQueryError: The database rejected or cancelled the statement.
    """
    statement, bound = parameters.bind(sql_text, values)
    connection = connections[ANALYTICS_CONNECTION]
    try:
        with connection.cursor() as cursor:
            # Each SET LOCAL lasts only as long as the transaction, so neither setting
            # leaks into whatever reuses this pooled connection next.
            cursor.execute("BEGIN READ ONLY")
            try:
                cursor.execute("SET LOCAL statement_timeout = %s", [STATEMENT_TIMEOUT])
                # The query's text is sent on its own, never appended to the statements
                # above, so it cannot begin with a SET that lifts the read-only mode.
                cursor.execute(statement, bound or None)
                columns = [column.name for column in cursor.description or []]
                rows = cursor.fetchall() if cursor.description else []
            finally:
                # Nothing to keep from a read-only transaction; the rollback also clears
                # one that a failed statement left aborted.
                cursor.execute("ROLLBACK")
    except Exception as error:
        raise PanelQueryError(str(error).strip().splitlines()[0]) from error

    return columns, rows
