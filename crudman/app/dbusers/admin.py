"""The page a person creates their own access token on.

Under Access, beside the users and groups: the token is how somebody reaches the database
from their own machine, which is the same question that section already answers. An admin
page rather than one of its own so it appears in the sidebar without a template override,
and behind the same sign-in as everything else here.
"""
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.template.response import TemplateResponse
from django.urls import path
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
        """Shown to everyone signed in, so the entry is there for the page's audience.

        The default asks whether the person holds any permission in the app this proxy
        borrowed its label from, and the ranks deliberately hold none in auth
        (MANAGED_APPS in sso/roles.py) -- which would hide the entry from exactly the
        people who need it.
        """
        return request.user.is_authenticated

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
