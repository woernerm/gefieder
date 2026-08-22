"""The allauth shim: read the provider's roles, apply them, refuse anyone without one.

Kept to the two documented hooks that bracket a login, so the reconciliation in roles.py
runs whether the account already existed or was created by this login. The work itself
lives there, free of any allauth import, which keeps it testable with single sign-on
switched off.
"""
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.core.exceptions import PermissionDenied

from .roles import apply_roles, claimed_roles, highest_role


class SSOAccountAdapter(DefaultSocialAccountAdapter):
    """Applies the provider's roles on every login, not just the first one."""

    def pre_social_login(self, request, sociallogin):
        # Authenticating is not the same as having been granted access: someone in the
        # directory holding none of the roles is turned away here, rather than being let
        # in with whatever an earlier login left behind.
        roles = claimed_roles(sociallogin.account.extra_data)
        if highest_role(roles) is None:
            raise PermissionDenied(
                "Your account has no role for this system. Ask an administrator to assign one."
            )

        # Reconciled before the session starts, so a role taken away in the directory is
        # gone from this login rather than the next. New accounts have no primary key
        # yet and are handled in save_user below.
        if sociallogin.is_existing:
            apply_roles(sociallogin.user, roles)

    def save_user(self, request, sociallogin, form=None):
        user = super().save_user(request, sociallogin, form)
        apply_roles(user, claimed_roles(sociallogin.account.extra_data))
        return user
