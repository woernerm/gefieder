"""Keeping the database role in step with the Django account it belongs to.

Both receivers swallow failures: the next login corrects the role, whereas propagating
would turn a database hiccup into a failed login.
"""
import logging

logger = logging.getLogger(__name__)


def sync_on_login(sender, request, user, **kwargs):
    """Reconcile the person's database rank with the one they now hold.

    No password is handed over here. One is issued when it is going to be used -- by a
    notebook spawn, or by the person's own checkout with their access token -- and expires
    on its own, so there is nothing for them to copy down and nothing to leak from a
    message they scrolled past.

    Args:
        sender: The signal sender, unused.
        request: The request, unused.
        user: The person signing in.
        **kwargs: The remaining signal arguments, all unused.
    """
    from .utils import sync

    try:
        sync(user)
    except Exception:
        logger.exception("Could not sync the database role for %s", user.username)


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
