"""Keeping the database role in step with the Django account it belongs to.

Both receivers swallow failures: the next login corrects the role, whereas propagating
would turn a database hiccup into a failed login.
"""
import logging

logger = logging.getLogger(__name__)


def sync_on_login(sender, request, user, **kwargs):
    """Reconcile the person's database rank, and hand them a password if one is owed.

    Issued here rather than at enrollment: this is the only moment the account's owner is
    the one looking at the screen.

    Args:
        sender: The signal sender, unused.
        request: The request to attach the password message to.
        user: The person signing in.
        **kwargs: The remaining signal arguments, all unused.
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

    # A warning rather than a success message: the password is shown only this once.
    backend = get_backend()
    role_name = user.database_user.role_name
    messages.warning(
        request,
        f"Your database user is {role_name} and password: {secret} — copy it now. "
        f"It will not be shown again. {backend.connection_hint(role_name)}",
    )


def disable_on_user_delete(sender, instance, **kwargs):
    """Disable the database role of an administrator being removed.

    Not dropped, so what they created keeps its owner; see ``delete_db_user`` in
    postgresql/initdb/gf_0003.

    Args:
        sender: The signal sender, unused.
        instance: The Django user being deleted.
        **kwargs: The remaining signal arguments, all unused.
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
