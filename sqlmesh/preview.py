"""Publish a planned environment under the schema names production uses.

    python preview.py <environment>

SQLMesh plans a version under review into schemas named ``<layer>__<environment>``. The
preview database holds foreign tables named like production's, pointing at the
environment's copy where there is one and at production otherwise. So a dashboard
reading ``gold.issue_metrics`` gets the reviewed models unchanged.

The work is done by ``refresh_preview`` (postgresql/initdb/gf_0009); the engine calls it,
as it knows when a plan has finished.
"""

import os
import sys

from status import connect


def main() -> int:
    """Publish the environment named by the first argument in its preview database."""
    environment = sys.argv[1]
    # Named after the environment it serves, so a second review is a second database
    # and no new rule.
    connection = connect(f"{os.environ.get('POSTGRES_DB', 'postgres')}_{environment}")

    with connection, connection.cursor() as cursor:
        cursor.execute("SELECT refresh_preview(%s)", (environment,))

    connection.close()
    print(f"Published the {environment} environment under the production schema names.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
