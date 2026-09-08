"""The bridge between the DatabaseUser model and the PostgreSQL functions.

Those functions are SECURITY DEFINER (``postgresql/initdb/gf_0003``) because creating a
role needs CREATEROLE, which crudman does not have and should not be given.
"""
import os
import re
import secrets
from datetime import datetime, timedelta

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

    A derived name can land on a role this app did not create -- a service role, or one an
    operator made by hand. The provisioning functions refuse to touch a role without their
    marker, so this is what tells a caller before one of them raises.

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
    """Create a user's database role, with no password of its own.

    The administrator decides *that* someone gets database access; the password comes
    later and belongs to the person alone -- ``issue_password``, called by a notebook
    spawn or by their own checkout. So no administrator ever learns one, and under
    scram-sha-256 the role cannot connect until its owner asks for a password.

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
            # NULL: the role exists and holds its rank, but cannot connect until its
            # owner has a password issued to them.
            cursor.execute(
                "SELECT create_db_user(%s, %s, %s)", [role_name, None, group_role]
            )

        record, _ = DatabaseUser.objects.update_or_create(
            user=user,
            defaults={
                "role_name": role_name,
                "group_role": group_role,
                "is_enabled": True,
            },
        )

    return record


def reset(user) -> None:
    """Clear a person's database password and the token that would mint another.

    For a credential that reached the wrong place. Both halves go together: clearing the
    password alone would leave a token able to issue a fresh one immediately.

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

        record.token = ""
        record.save(update_fields=["token", "provisioned_on"])


def issue_password(user, valid_for: timedelta) -> tuple[str, str, datetime]:
    """Set a fresh, expiring password on a person's own database role.

    What a notebook spawn and a developer's checkout both call. The role is the person's
    own, so there is no second account to grant anything between: current_user in the
    session is them.

    Issuing replaces the previous password, so a person holds one at a time. That is what
    keeps a leaked one bounded, and why the caller is expected to be the person's own
    client rather than something that hands the result on.

    Args:
        user: The Django user whose role gets the password.
        valid_for: How long it stays usable.

    Returns:
        The person's role name, the password, and the instant it expires.

    Raises:
        ValueError: The person has no usable database account. Provisioning one is an
            administrator's deliberate act, so this reports rather than creates.
    """
    from .models import DatabaseUser

    record = DatabaseUser.objects.filter(user=user, is_enabled=True).first()
    if record is None:
        raise ValueError(
            f"{user.username} has no database account. An administrator switches Database "
            "access on for them."
        )

    password = get_backend().make_secret()
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT issue_db_user_password(%s, %s, %s)",
            [record.role_name, password, valid_for],
        )
        return record.role_name, password, cursor.fetchone()[0]


def issue_token(user) -> str:
    """Create or replace the token a person's checkout authenticates with.

    One per person: issuing again invalidates whatever they had, which is how a token that
    reached the wrong place is taken back without touching their account.

    Args:
        user: The Django user the token belongs to.

    Returns:
        The token, shown once. Only its owner ever sees it.

    Raises:
        ValueError: The user has no database account to attach it to.
    """
    from .models import DatabaseUser

    record = DatabaseUser.objects.filter(user=user).first()
    if record is None:
        raise ValueError(f"{user.username} has no database account.")

    record.token = secrets.token_urlsafe(32)
    record.save(update_fields=["token", "provisioned_on"])
    return record.token


def user_for_token(token: str):
    """Whose token this is, if it is anyone's.

    Args:
        token: The token presented by a checkout.

    Returns:
        The Django user, or None. A disabled account matches nothing, so switching Database
        access off revokes the token by the same click.
    """
    from .models import DatabaseUser

    if not token:
        return None

    record = DatabaseUser.objects.filter(token=token, is_enabled=True).first()
    return record.user if record and record.user.is_active else None


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

        # The token mints passwords, so it must not outlive the access it stands for.
        DatabaseUser.objects.filter(role_name=role_name).update(
            is_enabled=False, token=""
        )


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
