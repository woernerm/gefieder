"""The bridge between the DatabaseUser model and the PostgreSQL functions.

The functions called here are SECURITY DEFINER (``postgresql/initdb/gf_0003``) because
creating a role needs CREATEROLE, which crudman does not have and should not be given —
the same arrangement the tenants app uses.
"""
import os
import re

from django.db import connection, transaction
from sso.roles import GROUP_FOR_RANK, RANKS

from .backends import get_backend

DB_ROLE_PREFIX = os.environ.get("DB_ROLE_PREFIX", "gf_")
"""The prefix the database was initialised with, from DB_ROLE_PREFIX in buildtime.env.

Both sides have to agree: a role this module names is one gf_0008 created. The fallback
is the value buildtime.env ships, for a checkout run without the quadlet.
"""

GROUP_TO_DB_ROLE = {
    GROUP_FOR_RANK[rank]: f"{DB_ROLE_PREFIX}{rank}" for rank in RANKS
}
"""The database group role each single sign-on group earns.

The one place the groups in sso/roles.py and the group roles in gf_0008 meet. Neither
set is listed again: they share the three rank names behind their own prefixes, so a rank
added to ``sso.roles.RANKS`` reaches the database rank of the same name. Viewers are
included so read-only access is a provisioned account rather than a shared credential.
"""

ROLE_PREFIX = f"{DB_ROLE_PREFIX}u_"
"""Prefix every provisioned role carries, one level below the group roles.

It keeps these roles apart from the service and tenant roles sharing the namespace, so a
person called "grafana" cannot collide with the service of that name.
"""


def role_name_for(username: str) -> str:
    """The PostgreSQL role name for a Django username.

    Args:
        username: The Django username, frequently an email address from an identity
            provider and so not a valid identifier.

    Returns:
        The prefixed slug, anything outside ``[a-z0-9_]`` collapsed to an underscore as
        tenants does it. The prefix keeps it from starting with a digit.
    """
    slug = re.sub(r"[^a-z0-9]+", "_", username.strip().lower()).strip("_")
    # 63 is PostgreSQL's identifier limit; the database function caps the part it is given
    # at 50, so the slug is trimmed to fit that with the prefix.
    return f"{ROLE_PREFIX}{slug}"[:50]


def db_role_for_user(user) -> str | None:
    """The group role a Django user's groups earn them.

    Args:
        user: The Django user.

    Returns:
        The database group role, or None if they hold none. Someone may hold several
        managed groups at once; the most privileged wins, as ``sso.roles.highest_role``
        resolves the same ambiguity. A superuser earns the admin rank whatever their
        groups say: with single sign-on off nothing grants a role group, and the local
        administrator would otherwise be the one person unable to reach the database.
    """
    names = set(user.groups.values_list("name", flat=True))
    # RANKS runs from least to most privileged, so walking it backwards returns the
    # highest the person holds.
    for rank in reversed(RANKS):
        group = GROUP_FOR_RANK[rank]
        if group in names:
            return GROUP_TO_DB_ROLE[group]

    if user.is_superuser:
        return GROUP_TO_DB_ROLE[GROUP_FOR_RANK["admin"]]
    return None


def enroll(user) -> "DatabaseUser":
    """Create a user's database role without a credential, ready to be claimed.

    The administrator's half of provisioning: they decide *that* someone gets database
    access, while ``issue_credential`` generates the password on that person's next
    sign-in. So an administrator never learns a password that is not theirs, and no
    readable secret ever sits in a table. Under scram-sha-256 a role carrying no password
    cannot authenticate, so the early role grants nothing.

    Args:
        user: The Django user to enroll.

    Returns:
        The DatabaseUser row recording the role.

    Raises:
        ValueError: The user is in none of the roles that grant database access.
    """
    from .models import DatabaseUser

    group_role = db_role_for_user(user)
    if group_role is None:
        raise ValueError(
            f"{user.username} is in none of the roles that grant database access."
        )

    role_name = role_name_for(user.username)

    # A role created without its DatabaseUser row would be invisible to the admin, and a
    # row without its role would promise access that does not exist.
    with transaction.atomic():
        with connection.cursor() as cursor:
            # A NULL password is what "not claimed yet" looks like in the database.
            cursor.execute(
                "SELECT create_db_user(%s, %s, %s)", [role_name, None, group_role]
            )

        record, _ = DatabaseUser.objects.update_or_create(
            user=user,
            defaults={
                "role_name": role_name,
                "group_role": group_role,
                "is_enabled": True,
                "awaiting_credential": True,
            },
        )

    return record


def issue_credential(user) -> str | None:
    """Generate and set the password for a role that is waiting for one.

    Called from the login signal, so the person who owns the account is the one looking
    at the screen when it appears.

    Args:
        user: The Django user signing in.

    Returns:
        The secret, shown once and afterwards unrecoverable — PostgreSQL keeps only a
        SCRAM verifier and this app keeps nothing. None when there is nothing to issue:
        no account, one already claimed, or a backend whose provider holds the
        credential.
    """
    from .models import DatabaseUser

    record = DatabaseUser.objects.filter(user=user, awaiting_credential=True).first()
    if record is None:
        return None

    backend = get_backend()
    if not backend.issues_secret:
        # Nothing to hand over, so the account is claimed the moment it is enrolled.
        record.awaiting_credential = False
        record.save(update_fields=["awaiting_credential", "provisioned_on"])
        return None

    secret = backend.make_secret()

    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT create_db_user(%s, %s, %s)",
                [record.role_name, secret, record.group_role],
            )

        record.awaiting_credential = False
        record.is_enabled = True
        record.save(
            update_fields=["awaiting_credential", "is_enabled", "provisioned_on"]
        )

    return secret


def reset(user) -> None:
    """Put a role back into the waiting state so a new password is issued on next sign-in.

    How a forgotten password is dealt with. The old one is cleared immediately, so an
    account whose credential may have leaked stops working at once.

    Args:
        user: The Django user whose credential is reset.

    Raises:
        ValueError: The user has no database account.
    """
    from .models import DatabaseUser

    record = DatabaseUser.objects.filter(user=user).first()
    if record is None:
        raise ValueError(f"{user.username} has no database account.")

    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT clear_db_user_password(%s)", [record.role_name]
            )

        record.awaiting_credential = True
        record.save(update_fields=["awaiting_credential", "provisioned_on"])


def disable(role_name: str) -> None:
    """Take away a role's ability to connect, keeping everything it owns.

    Used when someone loses their role in the identity provider or their Django account
    is removed. Deliberately not a DROP; see ``delete_db_user`` in gf_0003.

    Args:
        role_name: The PostgreSQL role to disable.
    """
    from .models import DatabaseUser

    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SELECT delete_db_user(%s)", [role_name])

        DatabaseUser.objects.filter(role_name=role_name).update(is_enabled=False)


def sync(user) -> None:
    """Bring a person's database role in line with the rank they now hold.

    Called on every login, so a promotion or demotion in the identity provider reaches
    the database on the next sign-in. Someone who has lost every role is disabled;
    someone who never had a database user is left alone, because provisioning has to be a
    deliberate act by an administrator, not a side effect of logging in.

    Args:
        user: The Django user signing in.
    """
    from .models import DatabaseUser

    record = DatabaseUser.objects.filter(user=user).first()
    if record is None:
        return

    group_role = db_role_for_user(user) if user.is_active else None

    if group_role is None:
        if record.is_enabled:
            disable(record.role_name)
        return

    if group_role == record.group_role and record.is_enabled:
        return

    # create_db_user takes a NULL password as "leave the credential alone", so a rank
    # change rewrites the group membership and the person keeps the password they have.
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT create_db_user(%s, %s, %s)",
                [record.role_name, None, group_role],
            )

        record.group_role = group_role
        record.is_enabled = True
        record.save(update_fields=["group_role", "is_enabled", "provisioned_on"])


def remove(user) -> None:
    """Drop a person's database role and forget the account entirely.

    The destructive counterpart to ``disable``, for an account created by mistake or a
    departure where nothing the person owned is worth keeping. PostgreSQL refuses to drop
    a role that still owns objects, so any table they created goes with it. Disabling is
    the right choice in most departures; this is the exception.

    Args:
        user: The Django user whose database account is dropped.

    Raises:
        ValueError: The user has no database account.
    """
    from .models import DatabaseUser

    record = DatabaseUser.objects.filter(user=user).first()
    if record is None:
        raise ValueError(f"{user.username} has no database account.")

    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SELECT drop_db_user(%s)", [record.role_name])

        record.delete()
