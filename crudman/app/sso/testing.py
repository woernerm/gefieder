"""Test support shared by the apps whose tests need people of a given rank."""
from django.contrib.auth.models import Group, User

from .roles import GROUP_FOR_RANK


def make_user(username, rank=None, **flags):
    """A user with password "x", in the group of one rank when given ("viewer", ...)."""
    user = User.objects.create_user(username, password="x", **flags)
    if rank:
        user.groups.add(Group.objects.get_or_create(name=GROUP_FOR_RANK[rank])[0])
    return user
