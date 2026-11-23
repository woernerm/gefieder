"""Report the outcome of a deployment back to the row crudman created for it.

    python status.py <environment> <sha> transforming
    python status.py <environment> <sha> documenting
    python status.py <environment> <sha> succeeded docs.json
    ... | python status.py <environment> <sha> failed

The environment is needed besides the commit, as the same commit is planned into
production and into a review as separate rows. crudman opens the row; this names each step
before it starts, so the page can say which one takes the time, and closes it.

Only a failure reads standard input, to keep the tail of the plan log.

Raw SQL, as the engine image has neither Django nor crudman. The engine's role is granted
exactly SELECT and UPDATE on the one table.
"""

import json
import os
import re
import sys
from pathlib import Path

import psycopg2

SUCCEEDED, FAILED = "succeeded", "failed"

FINISHED = (SUCCEEDED, FAILED)
"""The statuses nothing follows; the row is only updated while it is on none of them."""

MESSAGE_LIMIT = 4000
"""How much of a failed plan's log to keep. The tail, that being where it says why."""

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
"""The colour codes SQLMesh writes for a terminal; a browser renders them as text."""


def connect(database: str = ""):
    """A connection as the engine's role, to the warehouse unless another database is named.

    Shared with preview.py and the entrypoint's wait for the database.
    """
    secret = Path("/run/secrets", os.environ.get("SECRET_SQLMESH_PASSWORD", "sqlmesh_password"))
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=os.environ.get("POSTGRES_PORT", "5432"),
        dbname=database or os.environ.get("POSTGRES_DB", "postgres"),
        user=os.environ.get("POSTGRES_USER", "sqlmesh"),
        password=secret.read_text().strip(),
    )


def failure_message(log: str) -> str:
    """What to show a person about a plan that did not work.

    Args:
        log: Everything the plan wrote.

    Returns:
        The tail, stripped of terminal colour codes. The tail because SQLMesh reports the
        error last, after however many lines of progress it took to get there.
    """
    return ANSI.sub("", log).strip()[-MESSAGE_LIMIT:]


def main() -> int:
    """Record the step or outcome given on the command line; see the module usage."""
    environment, sha, status = sys.argv[1], sys.argv[2], sys.argv[3]
    docs = json.loads(Path(sys.argv[4]).read_text()) if len(sys.argv) > 4 else {}
    # Only on a failure: the other calls are not given a pipe, and reading a standard
    # input nobody is writing would wait for the container's own.
    message = failure_message(sys.stdin.read()) if status == FAILED else ""

    connection = connect()

    # The newest unfinished row for this commit: deploying the same commit again creates a
    # new row, and this must advance that one rather than an older attempt.
    with connection, connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE crudman.system_deployment
               SET status = %s,
                   message = %s,
                   docs = %s::jsonb,
                   -- Stamped only when the deployment is over, a step in between being a
                   -- report of progress rather than of an outcome.
                   applied_on = CASE WHEN %s THEN now() ELSE applied_on END
             WHERE id = (
                     SELECT id FROM crudman.system_deployment
                      WHERE sha = %s AND environment = %s AND NOT (status = ANY(%s))
                      ORDER BY created_on DESC LIMIT 1
                   )
            """,
            (status, message, json.dumps(docs), status in FINISHED, sha, environment,
             list(FINISHED)),
        )
        updated = cursor.rowcount

    connection.close()

    # Not an error: the engine also starts against a tree that was put there before this
    # version of crudman existed, and saying so beats failing the deployment over it.
    if not updated:
        print(f"No unfinished {environment} deployment recorded for {sha[:8]}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
