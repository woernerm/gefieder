"""The bridge between the DatabaseUser model and the PostgreSQL functions.

The functions called here are SECURITY DEFINER (``create_db_user`` and ``delete_db_user``
in ``postgresql/initdb/gf_0003_create_functions.sql``) because creating a role needs
CREATEROLE, which crudman does not have and should not be given -- the same arrangement
the tenants app uses, for the same reason.
"""
import re

from django.db import connection, transaction

from .backends import get_backend

# The single sign-on group a person is in decides the database rank they get. Both sides
# of this mapping are defined elsewhere -- the groups in sso/roles.py, the gf_* roles in
# postgresql/initdb/gf_0008_create_admin_roles.sql -- and this is the one place they meet.
#
# Viewers are included so that read-only access is still a provisioned account rather than
# a shared credential; what separates the ranks is write access to SQLMesh's schemas.
GROUP_TO_DB_ROLE = {
    "sso-viewer": "gf_viewer",
    "sso-editor": "gf_editor",
    "sso-admin": "gf_admin",
}

# Prefix every provisioned role carries. It keeps these roles apart from the service roles
# (crudman, sqlmesh, grafana) and the tenant roles, which share the same namespace, so a
# person called "grafana" cannot collide with the service of that name.
ROLE_PREFIX = "gf_u_"


def role_name_for(username: str) -> str:
    """The PostgreSQL role name for a Django username.

    Usernames from an identity provider are frequently email addresses, which are not
    valid identifiers, so the same slugging rule as tenants applies: anything outside
    ``[a-z0-9_]`` collapses to an underscore. The prefix guarantees the result never
    starts with a digit, so unlike the tenant slug no leading-digit fixup is needed.
    """
    slug = re.sub(r"[^a-z0-9]+", "_", username.strip().lower()).strip("_")
    # 63 is PostgreSQL's identifier limit; the database function caps the part it is given
    # at 50, so the slug is trimmed to fit that with the prefix.
    return f"{ROLE_PREFIX}{slug}"[:50]


def db_role_for_user(user) -> str | None:
    """The gf_* rank a Django user's groups earn them, or None if they have none.

    Someone may hold several managed groups at once; the most privileged wins, matching how
    sso.roles.highest_role resolves the same ambiguity.
    """
    names = set(user.groups.values_list("name", flat=True))
    for group in ("sso-admin", "sso-editor", "sso-viewer"):
        if group in names:
            return GROUP_TO_DB_ROLE[group]
    return None


def enroll(user) -> "DatabaseUser":
    """Create `user`'s database role without a credential, ready to be claimed.

    This is the administrator's half of provisioning, and it deliberately produces no
    password. An administrator decides *that* someone gets database access; the credential
    itself is generated on that person's next sign-in and shown only to them (see
    ``issue_credential``). Two things follow from splitting it this way: an administrator
    never learns a password that is not theirs, and nothing has to be stored in the
    meantime -- there is no window in which a readable secret sits in a table.

    With scram-sha-256 a role that carries no password cannot authenticate at all, so the
    role existing early grants nothing until the password is issued.
    """
    from .models import DatabaseUser

    group_role = db_role_for_user(user)
    if group_role is None:
        raise ValueError(
            f"{user.username} is in none of the roles that grant database access."
        )

    role_name = role_name_for(user.username)

    # The database call and the row that records it move together: a role created without
    # its DatabaseUser row would be invisible to the admin, and a row without its role
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

    Called from the login signal, so the person who owns the account is the one looking at
    the screen when it appears. The secret is returned rather than stored: it is shown once
    and is afterwards unrecoverable by design -- PostgreSQL keeps only a SCRAM verifier and
    this app keeps nothing.

    Returns None when there is nothing to issue: no account, one already claimed, or a
    backend that needs no secret because the identity provider holds the credential.
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

    How a forgotten password is dealt with: the old one is cleared immediately, so an
    account whose credential may have leaked stops working at once rather than when the
    person next signs in.
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

    Used when someone loses their role in the identity provider or their Django account is
    removed. Deliberately not a DROP: see the comment on delete_db_user in gf_0003.
    """
    from .models import DatabaseUser

    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SELECT delete_db_user(%s)", [role_name])

        DatabaseUser.objects.filter(role_name=role_name).update(is_enabled=False)


def sync(user) -> None:
    """Bring a person's database role in line with the rank they now hold.

    Called on every login, so a promotion or demotion in the identity provider reaches the
    database on the person's next sign-in rather than waiting for an operator. Someone who
    has lost every role is disabled; someone who never had a database user is left alone,
    because provisioning issues a credential and that has to be a deliberate act by an
    administrator, not a side effect of logging in.
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

    # A rank change re-runs provisioning without a new password: create_db_user takes a
    # NULL password as "leave the credential alone", so the person keeps the one they have
    # while their group membership is rewritten.
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

    The destructive counterpart to ``disable``: it exists for an account created by
    mistake, or a departure where nothing the person owned is worth keeping. Dropping a
    role means dropping what it owns -- PostgreSQL refuses otherwise -- so any model or
    table they created goes with it, along with the record of who made it. Disabling is
    the right choice in most departures; this is the exception.
    """
    from .models import DatabaseUser

    record = DatabaseUser.objects.filter(user=user).first()
    if record is None:
        raise ValueError(f"{user.username} has no database account.")

    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SELECT drop_db_user(%s)", [record.role_name])

        record.delete()
