"""Who may read the model documentation."""

from django.contrib.auth.mixins import UserPassesTestMixin
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.urls import reverse

from sso.roles import MANAGED_GROUPS


class ViewerRequiredMixin(UserPassesTestMixin):
    """Grants access from the viewer rank upwards.

    The groups are checked by the names ``sso.roles`` derives, so a deployment with a
    ROLE_PREFIX of its own still works. All three ranks are accepted because they are
    increasing privilege rather than cumulative membership -- an editor holds the editor
    group alone. Staff and superusers pass without a group, so the pages stay reachable
    where single sign-on is off.
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

        Sending someone who holds no rank to the login form cannot change the outcome.
        The same split as the dropzone upload page.
        """
        if not self.request.user.is_authenticated:
            return redirect_to_login(
                self.request.get_full_path(), reverse("admin:login")
            )
        raise PermissionDenied
