"""Test support shared by the apps whose tests need people of a given rank."""
from django.contrib.auth.models import Group, User
from django.utils import timezone

from .roles import GROUP_FOR_RANK


def make_user(username, rank=None, **flags):
    """A user with password "x", in the group of one rank when given ("viewer", ...).

    Signed in just now unless told otherwise, as anyone using the system has.
    """
    flags.setdefault("last_login", timezone.now())
    user = User.objects.create_user(username, password="x", **flags)
    if rank:
        user.groups.add(Group.objects.get_or_create(name=GROUP_FOR_RANK[rank])[0])
    return user
