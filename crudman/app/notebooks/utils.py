"""The database credential a spawned notebook server connects with.

The person's own login role, not an account of its own: current_user inside the notebook is
them, their rank applies unchanged, and everything a plan creates is owned by them. Nothing
has to be granted between two roles because there is only one.

The password is rotated at every spawn and expires by itself (``issue_db_user_password`` in
``postgresql/initdb/gf_0003``), which is what makes it safe to put in the environment of a
process: it is bounded rather than standing.
"""
from datetime import timedelta

from dbusers.utils import db_role_for_user, issue_password
from sso.roles import GROUP_FOR_RANK

EDITOR_GROUPS = frozenset({GROUP_FOR_RANK["editor"], GROUP_FOR_RANK["admin"]})
"""The ranks a notebook is open to.

Writing models means writing the warehouse, which is what separates an editor from a
viewer everywhere else in this system.
"""

CREDENTIAL_LIFETIME = timedelta(hours=12)
"""How long a notebook's password stays usable.

Longer than a working session, so a notebook left open over lunch still connects, and
short enough that a leaked environment stops being useful the same day.
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
        directly, so a superuser counts as an admin with single sign-on off.
    """
    from dbusers.models import DatabaseUser

    if not (user.is_active and db_role_for_user(user) in EDITOR_GROUPS):
        return False
    return DatabaseUser.objects.filter(user=user, is_enabled=True).exists()


def refusal(user) -> str:
    """Why this person may not open a notebook, in words they can act on.

    Shown by JupyterHub on the page they land on, so it has to name the next step rather
    than the rule that was broken.

    Args:
        user: The Django user being refused.

    Returns:
        The sentence to show them.
    """
    if db_role_for_user(user) not in EDITOR_GROUPS:
        return (
            "The notebooks are open from the editor rank upwards. An administrator "
            "grants that."
        )
    return (
        "You have no database account yet. An administrator switches Database access on "
        "for you."
    )


def issue_notebook_credential(user) -> tuple[str, str]:
    """Rotate the password of a person's own role and return what it connects with.

    Args:
        user: The Django user whose server is about to be spawned.

    Returns:
        The role name and its new password.

    Raises:
        ValueError: The person has no database account, or it is disabled.
    """
    role_name, password, _ = issue_password(user, CREDENTIAL_LIFETIME)
    return role_name, password
