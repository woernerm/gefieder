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
"""

import os

from django.db import connections

PANELS_CONNECTION = "panels"
"""The DATABASES alias that authenticates as the analytics (grafana) role."""

STATEMENT_TIMEOUT = os.environ.get("PANEL_STATEMENT_TIMEOUT", "30s")
"""How long a single panel query may run before the database cancels it."""


class PanelQueryError(Exception):
    """A panel's query could not be run, with a message fit to show the user."""


def run(sql, parameters=None):
    """Execute a panel's query and return its columns and rows.

    Args:
        sql: The statement to run, with ``%(name)s`` placeholders for parameters.
        parameters: Values to bind to those placeholders.

    Returns:
        A tuple of the column names and the list of row tuples.

    Raises:
        PanelQueryError: The database rejected or cancelled the statement.
    """
    connection = connections[PANELS_CONNECTION]
    try:
        with connection.cursor() as cursor:
            # Each SET LOCAL lasts only as long as the transaction, so neither setting
            # leaks into whatever reuses this pooled connection next.
            cursor.execute("BEGIN READ ONLY")
            try:
                cursor.execute("SET LOCAL statement_timeout = %s", [STATEMENT_TIMEOUT])
                # The panel's text is sent on its own, never appended to the statements
                # above, so it cannot begin with a SET that lifts the read-only mode.
                cursor.execute(sql, parameters or None)
                columns = [column.name for column in cursor.description or []]
                rows = cursor.fetchall() if cursor.description else []
            finally:
                # Nothing to keep from a read-only transaction; the rollback also clears
                # one that a failed statement left aborted.
                cursor.execute("ROLLBACK")
    except Exception as error:
        raise PanelQueryError(str(error).strip().splitlines()[0]) from error

    return columns, rows
