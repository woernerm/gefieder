"""The page a person creates their own access token on.

Reached from the menu behind one's own name at the foot of the sidebar, beside Change
password: a credential of one's own rather than a record to administer, and too small a
thing to hold a place in the app list. An admin page rather than a view of its own so it
sits behind the same sign-in and inside the same frame as everything else here.
"""
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.template.response import TemplateResponse
from django.urls import path, reverse
from unfold.admin import ModelAdmin

from .models import AccessToken
from .utils import issue_token


@admin.register(AccessToken)
class AccessTokenAdmin(ModelAdmin):
    """One page, showing the signed-in person their own token and nothing else.

    The changelist is replaced wholesale: a row here is somebody's database account, but
    the question the page answers is "how does my checkout connect", which is asked about
    oneself alone. An administrator grants the access; the credential that mints passwords
    is the account holder's.
    """

    # The row is provisioned by the Database access switch on the user page, and the token
    # is created by the button below. Nothing here is edited by hand.
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        """Anyone signed in, since the page only ever shows them their own token.

        Every rank is staff, and a viewer reaches the database too -- read-only, but from
        their own machine like anybody else.
        """
        return request.user.is_authenticated

    def has_module_permission(self, request):
        """Never listed: the way in is the user menu, see account_links below."""
        return False

    def get_urls(self):
        # Before the base URLs, whose "<path:object_id>/" would otherwise swallow this.
        return [
            path(
                "create/",
                self.admin_site.admin_view(self.create),
                name="dbusers_accesstoken_create",
            ),
            *super().get_urls(),
        ]

    def _page(self, request, **extra):
        """The page itself, with whatever the caller has to add to it."""
        return TemplateResponse(
            request,
            "dbusers/token.html",
            {
                **self.admin_site.each_context(request),
                "title": "Access token",
                "record": AccessToken.objects.filter(user=request.user).first(),
                **extra,
            },
        )

    def changelist_view(self, request, extra_context=None):
        if not self.has_view_permission(request):
            raise PermissionDenied
        return self._page(request, **(extra_context or {}))

    def create(self, request):
        """Issue a new token for the signed-in person, replacing whatever they had.

        Args:
            request: The admin request.

        Returns:
            The page, showing the token once.
        """
        if not self.has_view_permission(request):
            raise PermissionDenied
        if request.method != "POST":
            return self._page(request)

        try:
            secret = issue_token(request.user)
        except ValueError as error:
            messages.error(request, str(error))
            return self._page(request)

        # A warning rather than a success message: it is shown only this once.
        messages.warning(
            request, "The previous token, if there was one, no longer works."
        )
        return self._page(request, secret=secret)


def account_links(request):
    """The entries of the user menu, for UNFOLD["ACCOUNT"]["navigation"] in settings.

    Unfold replaces its own menu with whatever this returns, so the link it would have
    drawn -- Change password, for whoever signs in with one -- is put back here. Asked on
    the login page too, where nobody is signed in yet and the menu is not drawn.

    Args:
        request: The request being rendered.

    Returns:
        The links as Unfold expects them, title and link.
    """
    links = [
        {"title": "Access token", "link": reverse("admin:auth_accesstoken_changelist")}
    ]
    if request.user.is_authenticated and request.user.has_usable_password():
        links.append(
            {"title": "Change password", "link": reverse("admin:password_change")}
        )
    return links
