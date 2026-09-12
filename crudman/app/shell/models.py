"""Who is here: the count in the bar, and the list behind it."""
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db import models
from django.utils import timezone

ONLINE = timedelta(minutes=5)
"""How long after their last request a person still counts as online.

A dashboard left open refreshes itself and keeps its viewer here; a tab left behind falls
off the list within minutes rather than staying signed in for the fortnight the session
lasts.
"""

RECENT = timedelta(days=7)
"""How far back the list reaches for people who have been here lately."""

STAMP_EVERY = 60
"""Seconds between two writes for the same person.

Every Grafana panel refresh comes through the proxy's identity check, so without this a
dashboard refreshing every few seconds would be a row update every few seconds.
"""


class Presence(models.Model):
    """When a person was last seen.

    One row per person, overwritten rather than appended: the bar asks who is here now and
    who was this week, never when everybody was here -- the visit log answers that.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, primary_key=True, on_delete=models.CASCADE
    )
    last_seen = models.DateTimeField()


def seen(user):
    """Record that the person has just made a request, at most once a minute.

    Args:
        user: The signed-in user.
    """
    # add() succeeds only while no entry is there, which makes it the throttle.
    if cache.add(f"seen:{user.pk}", True, STAMP_EVERY):
        Presence.objects.update_or_create(
            user=user, defaults={"last_seen": timezone.now()}
        )


def lately():
    """Who is online, and who was this week, newest first.

    Returns:
        The rows under "online" and "recent", the latter excluding the former.
    """
    now = timezone.now()
    rows = (
        Presence.objects.filter(last_seen__gte=now - RECENT)
        .select_related("user")
        .order_by("-last_seen")
    )
    online = [row for row in rows if row.last_seen >= now - ONLINE]
    return {"online": online, "recent": rows[len(online):]}
