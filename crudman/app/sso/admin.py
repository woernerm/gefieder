"""One menu section for who may sign in and what they may do.

Users and groups are Django's, and are administered here whether single sign-on is on or
off: with it off they are made by hand, and with it on that is still how the local
superuser exists. Where they appear in the menu has nothing to do with this module -- the
sidebar groups models by the app that defines them, so it is the "auth" label that puts
them under one heading and AccessConfig in apps.py that names it.

What this module is really for is the rest of that heading. Single sign-on brings allauth,
and allauth registers four more pages under two further headings, all of them about
authentication and none of them saying so. Three are dead weight here, and the fourth is
worth keeping but not worth a menu entry of its own, so it comes back as an inline on the
user it describes.
"""
from django.conf import settings
from django.contrib import admin
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import Group, User
from unfold.admin import ModelAdmin, TabularInline
from unfold.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm

if settings.OIDC_ENABLED:
    # Imported for their side effect: each module registers its pages as it is imported,
    # and a page must be registered before it can be unregistered. Autodiscovery reaches
    # them only after this module -- allauth is appended to INSTALLED_APPS behind this app
    # -- so leaving it to do the importing would mean unregistering what is not there yet.
    import allauth.account.admin  # noqa: F401
    import allauth.socialaccount.admin  # noqa: F401
    from allauth.account.models import EmailAddress
    from allauth.socialaccount.models import SocialAccount, SocialApp, SocialToken

    # Social applications: the provider is configured in settings.py, from runtime.env, so
    #   a row added here would be a second and conflicting source for the same thing.
    # Social application tokens: SOCIALACCOUNT_STORE_TOKENS is off, so the page is empty.
    # Email addresses: no address is ever confirmed here, and the user already carries it.
    # Social accounts: kept, as the inline below rather than as a list beside the users.
    for model in (EmailAddress, SocialApp, SocialToken, SocialAccount):
        admin.site.unregister(model)

    class SingleSignOnInline(TabularInline):
        """The directory account behind a user, on the user rather than beside them.

        It exists to answer one question -- "the provider says I am an admin, so why am I
        not?" -- and the answer is in extra_data, the claims exactly as they arrived. Read
        only: every field belongs to the provider and is written again on the next login,
        and the row itself is what a returning login is recognised by, so removing it here
        would strand the account rather than tidy anything up.
        """

        model = SocialAccount
        verbose_name_plural = "Single sign-on"
        extra = 0
        can_delete = False
        fields = ("provider", "uid", "last_login", "extra_data")
        readonly_fields = fields

        def has_add_permission(self, request, obj=None):
            return False

        def has_change_permission(self, request, obj=None):
            return False


admin.site.unregister(User)
admin.site.unregister(Group)


@admin.register(User)
class UserAdmin(BaseUserAdmin, ModelAdmin):
    form = UserChangeForm
    add_form = UserCreationForm
    change_password_form = AdminPasswordChangeForm

    # Nothing to show without single sign-on, where the inline's model does not even exist.
    inlines = [SingleSignOnInline] if settings.OIDC_ENABLED else []

    def get_inlines(self, request, obj=None):
        # Someone who is being created here has signed in nowhere yet, so the add page
        # would carry an empty box -- and would then insist on getting its formset back
        # before it would save the new user.
        return super().get_inlines(request, obj) if obj else []


@admin.register(Group)
class GroupAdmin(BaseGroupAdmin, ModelAdmin):
    pass
