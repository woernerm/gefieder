"""Utility functions that bridge the fake ``Tenant`` model and the PostgreSQL
database functions defined in ``postgresql/initdb/gf_0003_create_functions.sql``.

A tenant is not a row in a table. It is a PostgreSQL login role that owns a
``<name>_bronze`` schema. These helpers create, configure and list tenants by calling
the corresponding database functions and by reading PostgreSQL's catalog, so the admin
interface can present tenants as if they were ordinary model instances.
"""
from django.db import connection

# Suffix every tenant's bronze schema carries; used to discover tenants from the catalog.
_BRONZE_SUFFIX = "_bronze"


def create_tenant(tenant_name: str, tenant_password: str) -> bool:
    """Call the ``create_tenant`` database function.

    Wraps the PostgreSQL function with the same name and parameters and returns whether
    the call succeeded. The database function does the input validation, role creation
    and schema setup; here we only forward the arguments.
    """
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT create_tenant(%s, %s)", [tenant_name, tenant_password])
        return True
    except Exception:
        return False


def set_tenant_limits(
    tenant_name: str,
    connection_limit: int | None = None,
    statement_timeout: str | None = None,
    work_mem: str | None = None,
    temp_file_limit: str | None = None,
) -> bool:
    """Apply resource limits to a tenant via the ``set_tenant_limits`` database function.

    A value of ``None`` means "no limit". PostgreSQL expresses that differently per
    setting: an unlimited connection limit is ``-1``, and unlimited memory/time is the
    string ``0``. We translate ``None`` to those sentinels so the limit fields can be
    left empty in the admin to mean infinite.
    """
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT set_tenant_limits(%s, %s, %s, %s, %s)",
                [
                    tenant_name,
                    -1 if connection_limit is None else connection_limit,
                    "0" if statement_timeout is None else statement_timeout,
                    "0" if work_mem is None else work_mem,
                    "0" if temp_file_limit is None else temp_file_limit,
                ],
            )
        return True
    except Exception:
        return False


def delete_tenant(tenant_name: str) -> bool:
    """Call the ``delete_tenant`` database function, dropping the role and its schema."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT delete_tenant(%s)", [tenant_name])
        return True
    except Exception:
        return False


def get_tenants() -> list:
    """Return one ``Tenant`` instance per tenant discovered in the database.

    Tenants are recognised by their ``<name>_bronze`` schema. The resource limits are
    read from PostgreSQL's catalog: the connection limit from ``pg_roles`` (``-1`` means
    unlimited) and the per-role ``statement_timeout``, ``work_mem`` and
    ``temp_file_limit`` from ``pg_db_role_setting`` (``0`` means unlimited). Unlimited
    values map back to ``None`` so the model mirrors how a tenant was created.
    """
    # Imported here to avoid a circular import at module load (models import nothing from
    # this module, but admin imports both).
    from .models import Tenant

    query = """
        SELECT
            left(s.schema_name, -%s) AS name,
            r.rolconnlimit,
            (SELECT split_part(c, '=', 2) FROM unnest(st.setconfig) c
                WHERE c LIKE 'statement_timeout=%%') AS statement_timeout,
            (SELECT split_part(c, '=', 2) FROM unnest(st.setconfig) c
                WHERE c LIKE 'work_mem=%%') AS work_mem,
            (SELECT split_part(c, '=', 2) FROM unnest(st.setconfig) c
                WHERE c LIKE 'temp_file_limit=%%') AS temp_file_limit
        FROM information_schema.schemata s
        JOIN pg_roles r ON r.rolname = left(s.schema_name, -%s)
        LEFT JOIN pg_db_role_setting st ON st.setrole = r.oid AND st.setdatabase = 0
        WHERE s.schema_name LIKE %s
        ORDER BY name
    """
    suffix_len = len(_BRONZE_SUFFIX)
    with connection.cursor() as cursor:
        cursor.execute(query, [suffix_len, suffix_len, f"%{_BRONZE_SUFFIX}"])
        rows = cursor.fetchall()

    tenants = []
    for name, conn_limit, statement_timeout, work_mem, temp_file_limit in rows:
        tenants.append(
            Tenant(
                name=name,
                # -1 / 0 are PostgreSQL's "unlimited" sentinels; show them as no limit.
                connection_limit=None if conn_limit in (-1, None) else conn_limit,
                statement_timeout=_unlimited_to_none(statement_timeout),
                work_mem=_unlimited_to_none(work_mem),
                temp_file_limit=_unlimited_to_none(temp_file_limit),
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
        Tenant.objects.update_or_create(
            name=tenant.name,
            defaults={
                "connection_limit": tenant.connection_limit,
                "statement_timeout": tenant.statement_timeout,
                "work_mem": tenant.work_mem,
                "temp_file_limit": tenant.temp_file_limit,
            },
        )
    Tenant.objects.exclude(name__in=names).delete()


def _unlimited_to_none(value: str | None) -> str | None:
    """Map PostgreSQL's unlimited sentinel ("0", or an unset value) to ``None``."""
    return None if value in (None, "", "0") else value
