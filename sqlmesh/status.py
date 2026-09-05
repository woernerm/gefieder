"""Report the outcome of a deployment back to the row crudman created for it.

    ... | python status.py <sha> succeeded|failed [docs.json]

crudman checks a commit out and records it as applying; this closes that row once the
engine has planned it. The plan log is piped in on standard input rather than passed as an
argument, and is kept only when the plan failed: a successful one says nothing a person
reading the page has to act on, and the page shows whatever is stored.

Raw SQL rather than Django: this runs in the engine image, which has neither Django nor
crudman in it. The engine's role is granted exactly SELECT and UPDATE on the one table.
"""

import json
import os
import re
import sys
from pathlib import Path

import psycopg2

SUCCEEDED = "succeeded"

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
    # Read either way, so the caller can pipe unconditionally.
    log = sys.stdin.read()
    message = "" if status == SUCCEEDED else failure_message(log)

    secret = Path("/run/secrets", os.environ.get("SECRET_SQLMESH_PASSWORD", "sqlmesh_password"))
    connection = psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=os.environ.get("POSTGRES_PORT", "5432"),
        dbname=os.environ.get("POSTGRES_DB", "postgres"),
        user=os.environ.get("POSTGRES_USER", "sqlmesh"),
        password=secret.read_text().strip(),
    )

    # The newest applying row for this commit: deploying the same commit again creates a
    # new row, and this must close that one rather than an older attempt.
    with connection, connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE crudman.system_deployment
               SET status = %s, message = %s, docs = %s::jsonb, applied_on = now()
             WHERE id = (
                     SELECT id FROM crudman.system_deployment
                      WHERE sha = %s AND status = 'pending'
                      ORDER BY created_on DESC LIMIT 1
                   )
            """,
            (status, message, json.dumps(docs), sha),
        )
        updated = cursor.rowcount

    connection.close()

    # Not an error: the engine also starts against a tree that was put there before this
    # version of crudman existed, and saying so beats failing the deployment over it.
    if not updated:
        print(f"No pending deployment recorded for {sha[:8]}; nothing to update.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
