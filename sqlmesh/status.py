"""Report the outcome of a deployment back to the row crudman created for it.

    python status.py <sha> transforming
    python status.py <sha> documenting
    python status.py <sha> succeeded docs.json
    ... | python status.py <sha> failed

crudman opens the row when it checks a commit out; this moves it through the steps the
engine does and closes it. Naming each step before starting it is what lets the page say
which one is taking the time.

Only a failure reads standard input, and only to keep the tail of the plan log: a plan
that worked says nothing a person reading the page has to act on, and the page shows
whatever is stored.

Raw SQL rather than Django: this runs in the engine image, which has neither Django nor
crudman in it. The engine's role is granted exactly SELECT and UPDATE on the one table.
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
"""The colour codes SQLMesh writes for a terminal.

The log reaches a browser rather than a terminal, where the codes are neither invisible
nor meaningful: they render as text in the middle of the sentence they were meant to
colour.
"""


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
    sha, status = sys.argv[1], sys.argv[2]
    docs = json.loads(Path(sys.argv[3]).read_text()) if len(sys.argv) > 3 else {}
    # Only on a failure: the other calls are not given a pipe, and reading a standard
    # input nobody is writing would wait for the container's own.
    message = failure_message(sys.stdin.read()) if status == FAILED else ""

    secret = Path("/run/secrets", os.environ.get("SECRET_SQLMESH_PASSWORD", "sqlmesh_password"))
    connection = psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=os.environ.get("POSTGRES_PORT", "5432"),
        dbname=os.environ.get("POSTGRES_DB", "postgres"),
        user=os.environ.get("POSTGRES_USER", "sqlmesh"),
        password=secret.read_text().strip(),
    )

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
                      WHERE sha = %s AND NOT (status = ANY(%s))
                      ORDER BY created_on DESC LIMIT 1
                   )
            """,
            (status, message, json.dumps(docs), status in FINISHED, sha, list(FINISHED)),
        )
        updated = cursor.rowcount

    connection.close()

    # Not an error: the engine also starts against a tree that was put there before this
    # version of crudman existed, and saying so beats failing the deployment over it.
    if not updated:
        print(f"No unfinished deployment recorded for {sha[:8]}; nothing to update.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
