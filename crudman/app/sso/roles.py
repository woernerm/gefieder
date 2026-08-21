"""Turning the identity provider's roles into Django rights.

The provider is the source of truth for *which* role someone holds; this project decides
what a role may do. That split is what keeps three unrelated permission models in step:
the provider only ever says "Editor", and the group named below carries the actual
permissions, edited in the admin like any other group.

The provider owns exactly the flags and the three groups named here, and nothing else.
Every other group and every per-user permission is left untouched, so rights granted by
hand survive each login. Reconciling rather than overwriting is the whole trick; see
apply_roles.

Deliberately free of any allauth import: the reconciliation is the part worth testing, and
it stays importable and testable whether or not single sign-on is switched on.
"""
import os

# The prefix on the group names, from SSO_GROUP_PREFIX in buildtime.env, which the crudman
# quadlet passes in. It exists so these groups can be kept clear of groups an admin already
# uses; the default is what buildtime.env ships, for a checkout run without the quadlet.
GROUP_PREFIX = os.environ.get("SSO_GROUP_PREFIX", "sso-")

# The provider's role names, in increasing order of privilege. The order matters, as the
# most privileged match wins. These are not configurable: what each may do is GROUP_ACTIONS
# below, and the database ranks in gf_0008 are the same three words behind DB_ROLE_PREFIX.
RANKS = ("viewer", "editor", "admin")

# Each rank mapped to the group that carries its permissions. Matching is case-insensitive
# because providers differ on capitalisation.
ROLE_GROUPS = tuple((rank, f"{GROUP_PREFIX}{rank}") for rank in RANKS)

# The group a rank grants, by rank name -- the lookup dbusers uses to line the database
# ranks up with these groups without spelling either set out again.
GROUP_FOR_RANK = dict(ROLE_GROUPS)

MANAGED_GROUPS = frozenset(group for _, group in ROLE_GROUPS)

# The claim carrying the role names. "roles" is what an Entra ID app role arrives in, and
# what Keycloak and Okta can be configured to send.
ROLE_CLAIM = "roles"

# What each group may do when it is first created. A starting point, not a policy: only the
# app's own models appear, because handing out "add_user" from django.contrib.auth would
# let an editor grant themselves anything. Delete stays with the admin role, the data being
# mostly imported evidence where losing a row is worse than being unable to remove one.
MANAGED_APPS = ("tenants", "dropzones")

GROUP_ACTIONS = {
    GROUP_FOR_RANK["viewer"]: ("view",),
    GROUP_FOR_RANK["editor"]: ("view", "add", "change"),
    GROUP_FOR_RANK["admin"]: ("view", "add", "change", "delete"),
}


def claimed_roles(extra_data):
    """The role names the provider sent, out of what allauth stored for the account.

    The ID token is read first. Entra ID puts app roles there and its userinfo endpoint
    omits them entirely, while allauth prefers userinfo when both are present -- so taking
    allauth's merged view of the account would silently find no roles at all.
    """
    data = extra_data or {}
    for source in (data.get("id_token"), data.get("userinfo"), data):
        if isinstance(source, dict) and source.get(ROLE_CLAIM):
            return source[ROLE_CLAIM]
    return []


def highest_role(claimed):
    """The most privileged known role in `claimed`, or None if it holds none.

    Someone can hold several roles at once -- an admin who is also listed as an editor --
    and gets the rights of the highest.
    """
    wanted = {str(role).strip().lower() for role in claimed or ()}
    match = None
    for role, group in ROLE_GROUPS:
        if role in wanted:
            match = group
    return match


def apply_roles(user, claimed):
    """Apply the provider's roles to `user` and report whether they may sign in.

    Returns the group the role granted, or None when the provider named no role this
    project knows. A user without a role is deactivated rather than left as they were: the
    usual reason for a role disappearing is that someone left, and the account should stop
    working on their next attempt rather than keep whatever it had.
    """
    granted = highest_role(claimed)

    # Only the managed groups are touched. The set difference is the point: a user's
    # membership of, say, "project-b-analysts" is invisible to this function, so a right
    # granted by hand in the admin outlives every login.
    from django.contrib.auth.models import Group

    current = set(user.groups.filter(name__in=MANAGED_GROUPS).values_list("name", flat=True))
    wanted = {granted} if granted else set()

    for name in current - wanted:
        user.groups.remove(Group.objects.get(name=name))
    for name in wanted - current:
        user.groups.add(Group.objects.get(name=name))

    # is_staff is what opens the admin at all, and is_superuser bypasses every permission
    # check -- so both are the provider's to give and, more to the point, to take away.
    user.is_active = granted is not None
    user.is_staff = granted is not None
    user.is_superuser = granted == GROUP_FOR_RANK["admin"]
    user.save(update_fields=["is_active", "is_staff", "is_superuser"])

    return granted


def create_role_groups(**kwargs):
    """Make sure the three groups exist, on post_migrate.

    Permissions are set only when a group is first created, so the edits an operator makes
    afterwards are not undone by the next deployment.

    A signal receiver rather than a data migration because Django creates the permissions
    themselves in post_migrate, which is to say after every migration has already run.
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
