"""Report the outcome of a deployment back to the row crudman created for it.

    ... | python status.py <sha> succeeded|failed [docs.json]

crudman checks a commit out and records it as applying; this closes that row once the
engine has planned it. The message is read from standard input, so a plan log is piped in
rather than passed as an argument.

Raw SQL rather than Django: this runs in the engine image, which has neither Django nor
crudman in it. The engine's role is granted exactly SELECT and UPDATE on the one table.
"""

import json
import os
import sys
from pathlib import Path

import psycopg2

MESSAGE_LIMIT = 4000
"""How much of the piped log to keep. The tail, that being where a failure says why."""


def main() -> int:
    sha, status = sys.argv[1], sys.argv[2]
    docs = json.loads(Path(sys.argv[3]).read_text()) if len(sys.argv) > 3 else {}
    message = sys.stdin.read().strip()[-MESSAGE_LIMIT:]

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
