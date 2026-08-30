"""Who may read the model documentation."""

from django.contrib.auth.mixins import UserPassesTestMixin
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.urls import reverse

from sso.roles import MANAGED_GROUPS


class ViewerRequiredMixin(UserPassesTestMixin):
    """Grants access from the viewer rank upwards.

    The rank groups are checked by the names ``sso.roles`` derives, not by the literal
    "viewer": a deployment that sets SSO_GROUP_PREFIX would otherwise lock out exactly
    the people the prefix was introduced for. All three ranks are accepted because they
    are increasing privilege rather than cumulative membership -- an editor holds the
    editor group alone and would fail a viewer-only test.

    Staff and superusers pass without a group, so the documentation stays reachable on a
    system where single sign-on is off and nobody has been assigned a rank yet.
    """

    def test_func(self) -> bool:
        user = self.request.user
        if not user.is_authenticated:
            return False
        return (
            user.is_superuser
            or user.is_staff
            or user.groups.filter(name__in=MANAGED_GROUPS).exists()
        )

    def handle_no_permission(self):
        """Sign an anonymous visitor in; refuse one who is already signed in.

        Sending someone who holds no rank back to the login page would loop them through
        a form that cannot change the outcome, so only the anonymous case redirects. The
        same split as the dropzone upload page.
        """
        if not self.request.user.is_authenticated:
            return redirect_to_login(
                self.request.get_full_path(), reverse("admin:login")
            )
        raise PermissionDenied
