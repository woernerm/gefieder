"""Utility functions that bridge the fake ``Tenant`` model and the PostgreSQL
database functions defined in ``postgresql/initdb/gf_0003_create_functions.sql``.

A tenant is not a row in a table. It is a PostgreSQL login role that owns a
``bronze_<name>`` schema. These helpers create, configure and list tenants by calling
the corresponding database functions and by reading PostgreSQL's catalog, so the admin
interface can present tenants as if they were ordinary model instances.
"""
import re

from django.db import connection, transaction

# Prefix every tenant's bronze schema carries; used to discover tenants from the catalog.
_BRONZE_PREFIX = "bronze_"


def slugify_tenant_name(display_name: str) -> str:
    """Turn a human tenant name like "Project A" into a PostgreSQL-safe slug "project_a".

    The slug becomes the tenant's role and bronze schema name, so it must satisfy the same
    rules the create_tenant database function enforces: only ``[a-z0-9_]`` and no leading
    digit. Spaces and other separators collapse to single underscores; a leading digit is
    prefixed with ``t_`` so the result is always a valid identifier.
    """
    slug = re.sub(r"[^a-z0-9]+", "_", display_name.strip().lower()).strip("_")
    if slug and slug[0].isdigit():
        slug = f"t_{slug}"
    return slug


def create_tenant(
    tenant_name: str, tenant_password: str, display_name: str = ""
) -> bool:
    """Call the ``create_tenant`` database function.

    Wraps the PostgreSQL function with the same name and parameters and returns whether
    the call succeeded. The database function does the input validation, role creation
    and schema setup; here we only forward the arguments. The human-readable display name
    is stored as a comment on the bronze schema so the catalog carries it too.
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

    "No limit" is expressed with PostgreSQL's sentinels: ``-1`` for the connection count
    and the string ``0`` for memory/time. A blank field arrives as ``None`` and is mapped
    to those sentinels too, so leaving a field empty in the admin also means infinite.
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
    """Update a tenant's human-readable name via the ``set_tenant_display_name`` function.

    Keeps the bronze schema's comment — the catalog's copy of the name — in sync when an
    admin renames an existing tenant, so a later changelist resync does not revert it.
    """
    return _call(
        "SELECT set_tenant_display_name(%s, %s)", [tenant_name, display_name]
    )


def delete_tenant(tenant_name: str) -> bool:
    """Call the ``delete_tenant`` database function, dropping the role and its schema."""
    return _call("SELECT delete_tenant(%s)", [tenant_name])


def _call(sql: str, params: list) -> bool:
    """Run a tenant database function, returning True on success and False on failure.

    The call is wrapped in its own atomic block so that a database error (e.g. a tenant
    that already exists) rolls back only this statement. Without the savepoint, the failed
    statement would leave the surrounding request transaction in an aborted state and every
    later query — including Django's own admin log write — would then fail too.
    """
    try:
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(sql, params)
        return True
    except Exception:
        return False


def get_tenants() -> list:
    """Return one ``Tenant`` instance per tenant discovered in the database.

    Tenants are recognised by their ``bronze_<name>`` schema. Schemas are read from
    ``pg_namespace`` rather than ``information_schema.schemata`` because the latter only
    lists schemas the connecting role (crudman) has privileges on, and the bronze schemas
    are owned by the tenant roles — so crudman would see none of them. The resource limits
    come from the catalog too: the connection limit from ``pg_roles`` and the per-role
    ``statement_timeout``, ``work_mem`` and ``temp_file_limit`` from
    ``pg_db_role_setting``. A missing value means the limit was reset to the server
    default, i.e. no per-tenant cap.
    """
    # Imported here to avoid a circular import at module load (models import nothing from
    # this module, but admin imports both).
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
                # The human name is stored as a COMMENT on the bronze schema by
                # create_tenant, so every tenant carries one — including those seeded
                # outside crudman. A schema without a comment (e.g. created before this
                # existed) reads as None; str() then falls back to the slug.
                display_name=display_name or "",
                # Represent "no limit" with the model's sentinels (-1 / "0") consistently:
                # an unset catalog value (None) also means no limit. Keeping the same
                # representation the add form and set_tenant_limits use means a tenant
                # looks identical right after creation and after a later changelist sync.
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
    admin changelist has a real queryset. Tenants present in the database are upserted and
    rows for tenants that no longer exist are removed.
    """
    from .models import Tenant

    tenants = get_tenants()
    names = [t.name for t in tenants]
    for tenant in tenants:
        # The catalog now carries the human name (a COMMENT on the bronze schema), so it is
        # part of the upsert: a resync reflects a name set or changed in PostgreSQL.
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
    """Return a size/time limit, or the unlimited sentinel "0" when none is set."""
    from .models import Tenant

    return Tenant.UNLIMITED_SIZE if value in (None, "") else value
