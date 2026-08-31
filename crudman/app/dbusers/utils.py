"""The bridge between the DatabaseUser model and the PostgreSQL functions.

Those functions are SECURITY DEFINER (``postgresql/initdb/gf_0003``) because creating a
role needs CREATEROLE, which crudman does not have and should not be given.
"""
import os
import re

from django.db import connection, transaction
from sso.roles import GROUP_FOR_RANK, RANKS

from .backends import get_backend

USER_PREFIX = os.environ.get("DB_USER_PREFIX", "gf_")
"""Prefix every provisioned login role carries, from DB_USER_PREFIX in buildtime.env.

Readability only, and so allowed to be empty: what marks a role as a personal account is
the marker create_db_user grants it, not its name.
"""


def role_name_for(username: str) -> str:
    """The PostgreSQL role name for a Django username.

    Args:
        username: The Django username, frequently an email address from an identity
            provider and so not a valid identifier.

    Returns:
        The prefixed slug, anything outside ``[a-z0-9_]`` collapsed to an underscore. The
        prefix keeps it from starting with a digit.
    """
    slug = re.sub(r"[^a-z0-9]+", "_", username.strip().lower()).strip("_")
    # The database function caps the name it is given at 50 characters.
    return f"{USER_PREFIX}{slug}"[:50]


def unmanaged_role(user) -> str | None:
    """The role a user already reaches the database through, when it is not ours to manage.

    A derived name can land on a role this app did not create -- the deployment's
    superuser is a Django account and a PostgreSQL role at once. The provisioning
    functions refuse to touch a role without their marker, so the switch reports the
    access without offering to remove it.

    Args:
        user: The Django user.

    Returns:
        The role name, or None when it is free or one this app provisioned.
    """
    from .models import DatabaseUser

    role_name = role_name_for(user.username)
    # This table rather than the is_db_user marker: readable from any database, whereas
    # the init scripts' functions live only in the deployment's own.
    if DatabaseUser.objects.filter(role_name=role_name).exists():
        return None

    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", [role_name])
        return role_name if cursor.fetchone() else None


def db_role_for_user(user) -> str | None:
    """The group role a Django user's groups earn them.

    Args:
        user: The Django user.

    Returns:
        The database group role, or None if they hold none. The most privileged of several
        groups wins, as ``sso.roles.highest_role`` resolves the same ambiguity. A
        superuser earns the admin rank whatever their groups say, since with single
        sign-on off nothing grants a role group.
    """
    names = set(user.groups.values_list("name", flat=True))
    # RANKS runs from least to most privileged, so backwards returns the highest held.
    # The Django group name is the database role name, both built from ROLE_PREFIX.
    for rank in reversed(RANKS):
        group = GROUP_FOR_RANK[rank]
        if group in names:
            return group

    if user.is_superuser:
        return GROUP_FOR_RANK["admin"]
    return None


def enroll(user) -> "DatabaseUser":
    """Create a user's database role without a credential, ready to be claimed.

    The administrator's half of provisioning: they decide *that* someone gets database
    access, while ``issue_credential`` generates the password on that person's next
    sign-in. So no administrator learns a password that is not theirs. Under
    scram-sha-256 a role carrying no password cannot authenticate.

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

    # A role without its row would be invisible to the admin, a row without its role
    # would promise access that does not exist.
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

    Called from the login signal, so the account's owner is the one looking at the
    screen.

    Args:
        user: The Django user signing in.

    Returns:
        The secret, unrecoverable afterwards: PostgreSQL keeps only a SCRAM verifier and
        this app keeps nothing. None when there is nothing to issue -- no account, one
        already claimed, or a backend whose provider holds the credential.
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

    The old password is cleared immediately, so a leaked credential stops working now.

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

    For someone who lost their role in the identity provider or whose Django account was
    removed. Deliberately not a DROP; see ``delete_db_user`` in gf_0003.

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

    Called on every login, so a promotion or demotion reaches the database at the next
    sign-in. Someone who has lost every role is disabled; someone who never had a database
    user is left alone, since provisioning must be a deliberate act by an administrator.

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

    # create_db_user reads a NULL password as "leave the credential alone".
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

    The destructive counterpart to ``disable``, for an account created by mistake.
    PostgreSQL refuses to drop a role that still owns objects, so any table they created
    goes with it.

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
