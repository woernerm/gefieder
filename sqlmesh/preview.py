"""Publish a planned environment under the schema names production uses.

    python preview.py <environment>

SQLMesh plans a version under review into an environment of its own, whose schemas are
named ``<layer>__<environment>``: only what the commit changes is built, everything else
stays the production table behind a view. That is the preview; this makes it readable.

The preview database holds nothing but foreign tables pointing back here, one schema per
production schema, named the same. This points each of them at the environment's copy
where there is one and at production where there is none, so a dashboard reading
``gold.issue_metrics`` gets the reviewed models without being written any differently.

The work is in ``refresh_preview`` (postgresql/initdb/gf_0009), which runs with the rights
to create schemas there. This is the call: the engine is what knows a plan has finished.
"""

import os
import sys

from status import connect


def main() -> int:
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
