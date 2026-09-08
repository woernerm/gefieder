"""The notebook login: the credential a spawned notebook server connects to the database as.

A sibling of the person's own login role rather than a second account: it is a member of
that role and holds nothing else (``create_notebook_login`` in
``postgresql/initdb/gf_0009``). The session assumes that role -- SQLMesh issues the SET ROLE
from its ``role`` connection setting -- so ``current_user`` inside the notebook is the
person, their rank applies unchanged, and everything a plan creates is owned by them.

Rotated at every spawn and never stored, which is what makes it safe to put in the
environment of a process: the password that reaches a laptop is a different one and stays
the person's alone.
"""
import secrets

from django.db import connection

from dbusers.utils import db_role_for_user
from sso.roles import GROUP_FOR_RANK

EDITOR_GROUPS = frozenset({GROUP_FOR_RANK["editor"], GROUP_FOR_RANK["admin"]})
"""The ranks a notebook is open to.

Writing models means writing the warehouse, which is what separates an editor from a
viewer everywhere else in this system.
"""


def may_use_notebooks(user) -> bool:
    """Whether this person can be given a notebook server at all.

    Both halves of what a spawn needs, so the link and the spawn agree: offering one that
    then fails is worse than not offering it.

    Args:
        user: The Django user.

    Returns:
        True for someone holding the editor rank upwards *and* a database account this
        system provisioned. The rank comes through ``dbusers`` rather than the groups
        directly, so a superuser counts as an admin with single sign-on off. The account
        is what the notebook's credential is a sibling of, and the deployment's superuser
        is the case that has the rank without one: their role is the cluster superuser,
        which is not this app's to manage and whose password is a podman secret.
    """
    from dbusers.models import DatabaseUser

    if not (user.is_active and db_role_for_user(user) in EDITOR_GROUPS):
        return False
    return DatabaseUser.objects.filter(
        user=user, is_enabled=True, awaiting_credential=False
    ).exists()


def refusal(user) -> str:
    """Why this person may not open a notebook, in words they can act on.

    Shown by JupyterHub on the page they land on, so it has to name the next step rather
    than the rule that was broken.

    Args:
        user: The Django user being refused.

    Returns:
        The sentence to show them.
    """
    from dbusers.models import DatabaseUser
    from dbusers.utils import unmanaged_role

    if db_role_for_user(user) not in EDITOR_GROUPS:
        return (
            "The notebooks are open from the editor rank upwards. An administrator "
            "grants that."
        )
    if unmanaged_role(user):
        return (
            f"{user.username} reaches the database through a role this system does not "
            "manage -- the deployment's superuser account -- so no notebook credential "
            "can be issued for it. Sign in as your own account instead."
        )
    if DatabaseUser.objects.filter(user=user, awaiting_credential=True).exists():
        return (
            "Your database password has not been issued yet. Sign out of the "
            "administration panel and back in; it is shown to you once, there."
        )
    return (
        "You have no database account yet. An administrator switches Database access on "
        "for you, and the password is issued at your next sign-in."
    )


def issue_notebook_credential(user) -> tuple[str, str, str]:
    """Rotate the notebook login of a person and return what it connects with.

    Args:
        user: The Django user whose server is about to be spawned.

    Returns:
        The notebook login, its new password, and the person's own role -- which the
        session assumes, so that what it creates is owned by them.

    Raises:
        ValueError: The person has no database account, or it is disabled. Provisioning one
            is an administrator's deliberate act, so this reports rather than creates.
    """
    from dbusers.models import DatabaseUser
    from dbusers.utils import unmanaged_role

    record = DatabaseUser.objects.filter(
        user=user, is_enabled=True, awaiting_credential=False
    ).first()
    if record is None:
        # The deployment's superuser is the case worth naming: their Django username maps
        # onto a PostgreSQL role that already exists and is not this app's to manage, so
        # the admin shows Database access as on while none can be provisioned. Their
        # credential is the podman secret, which must not reach a notebook's environment.
        if unmanaged_role(user):
            raise ValueError(
                f"{user.username} reaches the database through a role this system does "
                "not manage, so no notebook credential can be issued for it. Sign in as "
                "your own account instead: an administrator switches Database access on "
                "for it, and the password is issued at the next sign-in."
            )
        raise ValueError(
            f"{user.username} has no database account. An administrator switches Database "
            "access on for them, and the password is issued at their next sign-in."
        )

    # A password of this system's making whatever dbusers' backend is: it is never shown
    # to anyone, so there is no method for a provider to hold it instead.
    password = secrets.token_urlsafe(32)
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT create_notebook_login(%s, %s)", [record.role_name, password]
        )
        return cursor.fetchone()[0], password, record.role_name
