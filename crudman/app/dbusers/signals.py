"""Keeping the database role in step with the Django account it belongs to.

Both receivers are deliberately forgiving: a signed-in person whose database role cannot be
reached should still get their admin page. A failure here means the role is momentarily out
of step, which the next login corrects; letting it propagate would turn a database hiccup
into a failed login.
"""
import logging

logger = logging.getLogger(__name__)


def sync_on_login(sender, request, user, **kwargs):
    """Reconcile the person's database rank, and hand them a password if one is owed.

    The password is issued here rather than when an administrator enrolls the person,
    because this is the only moment the account's owner is the one looking at the screen.
    An administrator therefore never sees a credential that is not theirs, and none has to
    be stored while it waits to be collected.
    """
    from django.contrib import messages

    from .backends import get_backend
    from .utils import issue_credential, sync

    try:
        sync(user)
    except Exception:
        logger.exception("Could not sync the database role for %s", user.username)

    try:
        secret = issue_credential(user)
    except Exception:
        logger.exception("Could not issue a database password for %s", user.username)
        return

    if secret is None:
        return

    # A warning rather than a success message: it is the only time this password is ever
    # shown, and it has to survive being skimmed past.
    backend = get_backend()
    role_name = user.database_user.role_name
    messages.warning(
        request,
        f"Your database password: {secret} — copy it now. It is not stored anywhere "
        f"and cannot be shown again; an administrator can only issue a new one. "
        f"{backend.connection_hint(role_name)}",
    )


def disable_on_user_delete(sender, instance, **kwargs):
    """Disable the database role of an administrator being removed.

    The role is not dropped, so what they created keeps its owner; see delete_db_user in
    postgresql/initdb/gf_0003_create_functions.sql.
    """
    from .models import DatabaseUser
    from .utils import disable

    record = DatabaseUser.objects.filter(user=instance).first()
    if record is None:
        return

    try:
        disable(record.role_name)
    except Exception:
        logger.exception("Could not disable the database role %s", record.role_name)
