"""Turning the identity provider's roles into Django rights.

The provider decides which role someone holds; this project decides what a role may do.
It owns exactly the flags and the three groups named here, so every other group and every
per-user permission survives each login -- reconciling rather than overwriting, see
``apply_roles``. Free of any allauth import, so it stays testable with single sign-on off.
"""
import os

ROLE_PREFIX = os.environ.get("ROLE_PREFIX", "gf_")
"""Prefix on the rank names, from ROLE_PREFIX in buildtime.env.

It names both these groups and the database group roles gf_0008 creates, so the two agree
by construction, and keeps them clear of names already taken in the cluster.
"""

RANKS = ("viewer", "editor", "admin")
"""The provider's role names, in increasing order of privilege.

Not configurable: gf_0008 builds the database ranks from the same three words.
"""

ROLE_GROUPS = tuple((rank, f"{ROLE_PREFIX}{rank}") for rank in RANKS)
"""Each rank paired with the group that carries its permissions."""

GROUP_FOR_RANK = dict(ROLE_GROUPS)
"""The group a rank grants, by rank name; the lookup dbusers uses."""

MANAGED_GROUPS = frozenset(group for _, group in ROLE_GROUPS)
"""The only groups this module ever adds to or removes from a user."""

ROLE_CLAIM = "roles"
"""The claim carrying the role names.

Entra ID app roles arrive in it, and Keycloak and Okta can be configured to send it.
"""

MANAGED_APPS = ("tenants", "dropzones")
"""Apps whose permissions the managed groups may hold.

Not django.contrib.auth: "add_user" would let an editor grant themselves anything.
"""

GROUP_ACTIONS = {
    GROUP_FOR_RANK["viewer"]: ("view",),
    GROUP_FOR_RANK["editor"]: ("view", "add", "change"),
    GROUP_FOR_RANK["admin"]: ("view", "add", "change", "delete"),
}
"""What each group may do when it is first created; a starting point, not a policy.

Delete stays with the admin role: the data is mostly imported evidence, where losing a row
is worse than being unable to remove one.
"""


def claimed_roles(extra_data):
    """The role names the provider sent, out of what allauth stored for the account.

    Args:
        extra_data: The account's stored provider data.

    Returns:
        The claimed role names, or an empty list. The ID token is read first: Entra ID
        puts app roles there and omits them from userinfo, which allauth's merged view
        prefers.
    """
    data = extra_data or {}
    for source in (data.get("id_token"), data.get("userinfo"), data):
        if isinstance(source, dict) and source.get(ROLE_CLAIM):
            return source[ROLE_CLAIM]
    return []


def highest_role(claimed):
    """The group for the most privileged known role among those claimed.

    Args:
        claimed: The role names the provider sent.

    Returns:
        The group of the highest matching rank, so someone holding several roles gets the
        rights of the best; None if no role is known here.
    """
    wanted = {str(role).strip().lower() for role in claimed or ()}
    match = None
    for role, group in ROLE_GROUPS:
        if role in wanted:
            match = group
    return match


def apply_roles(user, claimed):
    """Apply the provider's roles to a user and report whether they may sign in.

    A user without a role is deactivated: the usual reason for a role disappearing is
    that someone left.

    Args:
        user: The user signing in.
        claimed: The role names the provider sent.

    Returns:
        The group the role granted, or None when the provider named no known role.
    """
    granted = highest_role(claimed)

    # A membership outside MANAGED_GROUPS is invisible to the set difference, so a right
    # granted by hand in the admin outlives every login.
    from django.contrib.auth.models import Group

    current = set(user.groups.filter(name__in=MANAGED_GROUPS).values_list("name", flat=True))
    wanted = {granted} if granted else set()

    for name in current - wanted:
        user.groups.remove(Group.objects.get(name=name))
    for name in wanted - current:
        user.groups.add(Group.objects.get(name=name))

    # is_staff opens the admin and is_superuser bypasses every permission check, so both
    # are the provider's to give and to take away.
    user.is_active = granted is not None
    user.is_staff = granted is not None
    user.is_superuser = granted == GROUP_FOR_RANK["admin"]
    user.save(update_fields=["is_active", "is_staff", "is_superuser"])

    return granted


def create_role_groups(**kwargs):
    """Make sure the three groups exist, on post_migrate.

    Permissions are set only on first creation, so an operator's later edits survive the
    next deployment. A receiver rather than a data migration because Django creates the
    permissions themselves in post_migrate.

    Args:
        **kwargs: The post_migrate signal arguments, all unused.
    """
    from django.contrib.auth.models import Group, Permission
    from django.db.models import Q

    for name, actions in GROUP_ACTIONS.items():
        group, created = Group.objects.get_or_create(name=name)
        if not created:
            continue

        codenames = Q()
        for action in actions:
            codenames |= Q(codename__startswith=f"{action}_")
        group.permissions.set(
            Permission.objects.filter(
                codenames, content_type__app_label__in=MANAGED_APPS
            )
        )
