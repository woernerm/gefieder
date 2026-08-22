"""The bridge between the ``Tenant`` model and the PostgreSQL functions in gf_0003.

A tenant is not a row in a table but a PostgreSQL login role owning a ``bronze_<name>``
schema. These helpers create, configure and list tenants by calling the database
functions and reading the catalog, so the admin can present tenants as ordinary model
instances.
"""
import os
import re

from django.db import connection, transaction

_BRONZE_PREFIX = os.environ.get("BRONZE_SCHEMA_PREFIX", "bronze_")
"""Prefix every tenant's bronze schema carries, used to discover tenants in the catalog.

From BRONZE_SCHEMA_PREFIX in buildtime.env, which render.sh baked into create_tenant and
the crudman quadlet passes in here; the two have to agree or no tenant is found.
"""


def slugify_tenant_name(display_name: str) -> str:
    """Turn a human tenant name like "Project A" into a PostgreSQL-safe slug "project_a".

    Args:
        display_name: The human-readable tenant name.

    Returns:
        The slug, which becomes the tenant's role and bronze schema name and so obeys
        the rules create_tenant enforces: only ``[a-z0-9_]`` and no leading digit, which
        is prefixed with ``t_``.
    """
    slug = re.sub(r"[^a-z0-9]+", "_", display_name.strip().lower()).strip("_")
    if slug and slug[0].isdigit():
        slug = f"t_{slug}"
    return slug


def create_tenant(
    tenant_name: str, tenant_password: str, display_name: str = ""
) -> bool:
    """Call the ``create_tenant`` database function, which does all the work.

    Args:
        tenant_name: The tenant's slug, used for its role and bronze schema.
        tenant_password: The new role's password.
        display_name: The human-readable name, stored as a comment on the bronze schema
            so the catalog carries it too.

    Returns:
        Whether the call succeeded.
    """
    return _call(
        "SELECT create_tenant(%s, %s, %s)",
        [tenant_name, tenant_password, display_name],
    )


def set_tenant_limits(
    tenant_name: str,
    connection_limit: int | None = None,
    statement_timeout: str | None = None,
    work_mem: str | None = None,
    temp_file_limit: str | None = None,
) -> bool:
    """Apply resource limits to a tenant via the ``set_tenant_limits`` database function.

    Args:
        tenant_name: The tenant's slug.
        connection_limit: Concurrent connections allowed.
        statement_timeout: Per-statement time limit.
        work_mem: Per-operation memory limit.
        temp_file_limit: Temporary file size limit.

    Returns:
        Whether the call succeeded. None means no limit and maps to PostgreSQL's
        sentinels (``-1`` for the count, ``"0"`` for the rest), so an empty admin field
        also means infinite.
    """
    return _call(
        "SELECT set_tenant_limits(%s, %s, %s, %s, %s)",
        [
            tenant_name,
            -1 if connection_limit is None else connection_limit,
            "0" if statement_timeout is None else statement_timeout,
            "0" if work_mem is None else work_mem,
            "0" if temp_file_limit is None else temp_file_limit,
        ],
    )


def set_tenant_display_name(tenant_name: str, display_name: str) -> bool:
    """Update a tenant's human-readable name via ``set_tenant_display_name``.

    Keeps the bronze schema's comment — the catalog's copy of the name — in sync, so a
    later changelist resync does not revert a rename.

    Args:
        tenant_name: The tenant's slug.
        display_name: The new human-readable name.

    Returns:
        Whether the call succeeded.
    """
    return _call(
        "SELECT set_tenant_display_name(%s, %s)", [tenant_name, display_name]
    )


def delete_tenant(tenant_name: str) -> bool:
    """Call the ``delete_tenant`` database function, dropping the role and its schema."""
    return _call("SELECT delete_tenant(%s)", [tenant_name])


def _call(sql: str, params: list) -> bool:
    """Run a tenant database function.

    The call gets its own atomic block so a database error rolls back only this
    statement. Without the savepoint it would leave the surrounding request transaction
    aborted, and every later query — Django's own admin log write included — would fail.

    Args:
        sql: The statement calling the function.
        params: Its parameters.

    Returns:
        True on success, False on any database error.
    """
    try:
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(sql, params)
        return True
    except Exception:
        return False


def get_tenants() -> list:
    """One unsaved ``Tenant`` instance per tenant discovered in the database.

    Tenants are recognised by their ``bronze_<name>`` schema, read from ``pg_namespace``
    rather than ``information_schema.schemata``: the latter lists only schemas crudman
    has privileges on, and the bronze schemas belong to the tenant roles.

    Returns:
        The tenants, with their limits read from ``pg_roles`` and ``pg_db_role_setting``.
        A missing value means no per-tenant cap.
    """
    # Imported here to avoid a circular import at module load.
    from .models import Tenant

    query = """
        SELECT
            substr(n.nspname, %s) AS name,
            obj_description(n.oid, 'pg_namespace') AS display_name,
            r.rolconnlimit,
            (SELECT split_part(c, '=', 2) FROM unnest(st.setconfig) c
                WHERE c LIKE 'statement_timeout=%%') AS statement_timeout,
            (SELECT split_part(c, '=', 2) FROM unnest(st.setconfig) c
                WHERE c LIKE 'work_mem=%%') AS work_mem,
            (SELECT split_part(c, '=', 2) FROM unnest(st.setconfig) c
                WHERE c LIKE 'temp_file_limit=%%') AS temp_file_limit
        FROM pg_catalog.pg_namespace n
        JOIN pg_catalog.pg_roles r ON r.rolname = substr(n.nspname, %s)
        LEFT JOIN pg_catalog.pg_db_role_setting st
            ON st.setrole = r.oid AND st.setdatabase = 0
        WHERE n.nspname LIKE %s
        ORDER BY name
    """
    # substr() is 1-based, so the tenant name starts one past the prefix length.
    name_start = len(_BRONZE_PREFIX) + 1
    with connection.cursor() as cursor:
        cursor.execute(query, [name_start, name_start, f"{_BRONZE_PREFIX}%"])
        rows = cursor.fetchall()

    tenants = []
    for name, display_name, conn_limit, statement_timeout, work_mem, temp_file_limit in rows:
        tenants.append(
            Tenant(
                name=name,
                # A schema without a comment reads as None; str() falls back to the slug.
                display_name=display_name or "",
                # An unset catalog value means no limit, spelled with the same sentinels
                # the add form and set_tenant_limits use, so a tenant looks identical
                # right after creation and after a later changelist sync.
                connection_limit=(
                    Tenant.UNLIMITED_COUNT if conn_limit is None else conn_limit
                ),
                statement_timeout=_size_or_unlimited(statement_timeout),
                work_mem=_size_or_unlimited(work_mem),
                temp_file_limit=_size_or_unlimited(temp_file_limit),
            )
        )
    return tenants


def sync_tenants() -> None:
    """Reconcile the ``Tenant`` cache table with the tenants found in PostgreSQL.

    The schemas and roles are the source of truth; this table only mirrors them so the
    admin changelist has a real queryset. Rows for tenants that no longer exist go.
    """
    from .models import Tenant

    tenants = get_tenants()
    names = [t.name for t in tenants]
    for tenant in tenants:
        # The catalog carries the human name too, so a resync reflects a name set or
        # changed in PostgreSQL.
        Tenant.objects.update_or_create(
            name=tenant.name,
            defaults={
                "display_name": tenant.display_name,
                "connection_limit": tenant.connection_limit,
                "statement_timeout": tenant.statement_timeout,
                "work_mem": tenant.work_mem,
                "temp_file_limit": tenant.temp_file_limit,
            },
        )
    Tenant.objects.exclude(name__in=names).delete()


def _size_or_unlimited(value: str | None) -> str:
    """A size or time limit, or the unlimited sentinel "0" when none is set."""
    from .models import Tenant

    return Tenant.UNLIMITED_SIZE if value in (None, "") else value
