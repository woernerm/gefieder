"""One menu section for who may sign in and what they may do.

Users and groups are Django's, and are administered here whether single sign-on is on or
off. What this module is really for is the rest of that heading: allauth registers four
more pages under two further headings, all about authentication and none saying so.
Three are dead weight here; the fourth comes back as an inline on the user it describes.

Database access is a switch on this page rather than a section of its own, because it is
one more thing a person either has or does not, alongside active and staff status. The
dbusers app keeps the model and the PostgreSQL bridge but registers no page: an operator
asks "who may reach the database", which is a column here, not a list elsewhere.
"""
import logging

from dbusers.utils import db_role_for_user, enroll, remove
from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import Group, User
from unfold.admin import ModelAdmin, TabularInline
from unfold.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm
from unfold.widgets import UnfoldBooleanSwitchWidget

logger = logging.getLogger(__name__)

if settings.OIDC_ENABLED:
    # Imported for their side effect: a page must be registered before it can be
    # unregistered, and autodiscovery reaches allauth only after this module, so leaving
    # it to do the importing would mean unregistering what is not there yet.
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

        It answers one question — "the provider says I am an admin, so why am I not?" —
        from extra_data, the claims exactly as they arrived. Read only: every field is
        written again on the next login, and the row is what a returning login is
        recognised by, so removing it would strand the account.
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


class DatabaseAccessFilter(admin.SimpleListFilter):
    """Filter the user list by whether a database account exists.

    A filter of its own rather than Django's boolean filter, because the flag is not a
    field on the user: it is the presence of the related dbusers row.
    """

    title = "database access"
    parameter_name = "database_access"

    def lookups(self, request, model_admin):
        return (("1", "Yes"), ("0", "No"))

    def queryset(self, request, queryset):
        if self.value() == "1":
            return queryset.filter(database_user__isnull=False)
        if self.value() == "0":
            return queryset.filter(database_user__isnull=True)
        return queryset


class UserWithDatabaseAccessForm(UserChangeForm):
    """The user form with the database-access switch added.

    Not a model field: the account lives in the dbusers table and, more to the point, in
    PostgreSQL, so the switch reports what exists rather than storing an intention. The
    save is what reconciles the two.
    """

    # Unfold's formfield_overrides reach model fields only, so the widget that makes the
    # switch look like is_staff beside it has to be named here.
    database_access = forms.BooleanField(
        label="Database access",
        required=False,
        widget=UnfoldBooleanSwitchWidget,
        help_text=(
            "A PostgreSQL login role of their own, with the privileges of their rank. "
            "The password is shown to them, once, the next time they sign in here."
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        user = self.instance
        self.fields["database_access"].initial = (
            user.pk is not None and hasattr(user, "database_user")
        )

        # Without a rank there is no privilege set to grant, so the switch would promise
        # an account that enroll() would refuse; saying so beats failing on save. A
        # superuser is exempt: with single sign-on off nobody carries a role group, and
        # the local administrator is then the one person who must still get an account.
        if user.pk is not None and db_role_for_user(user) is None and not user.is_superuser:
            self.fields["database_access"].disabled = True
            self.fields["database_access"].help_text = (
                "Unavailable: this user holds none of the roles that carry database "
                "privileges. Add them to one of the role groups below first."
            )


admin.site.unregister(User)
admin.site.unregister(Group)


@admin.register(User)
class UserAdmin(BaseUserAdmin, ModelAdmin):
    form = UserWithDatabaseAccessForm
    add_form = UserCreationForm
    change_password_form = AdminPasswordChangeForm

    # Both are stamped by Django on login and on creation; an edit here would only make
    # them disagree with what actually happened.
    readonly_fields = ("last_login", "date_joined")

    list_display = BaseUserAdmin.list_display + ("has_database_access",)
    list_filter = BaseUserAdmin.list_filter + (DatabaseAccessFilter,)

    # The switch sits with the other things a person either has or does not.
    fieldsets = (
        (None, {"fields": ("username", "password")}),
        ("Personal info", {"fields": ("first_name", "last_name", "email")}),
        (
            "Permissions",
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "database_access",
                    "groups",
                    "user_permissions",
                ),
            },
        ),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )

    # Nothing to show without single sign-on, where the inline's model does not even exist.
    inlines = [SingleSignOnInline] if settings.OIDC_ENABLED else []

    def get_queryset(self, request):
        # The column and the filter both ask about the related row; without this each
        # listed user costs a query of their own.
        return super().get_queryset(request).select_related("database_user")

    def get_inlines(self, request, obj=None):
        # Someone being created here has signed in nowhere yet, so the add page would
        # carry an empty box and then insist on its formset before saving the user.
        return super().get_inlines(request, obj) if obj else []

    @admin.display(description="database access", boolean=True)
    def has_database_access(self, obj):
        return hasattr(obj, "database_user")

    def save_model(self, request, obj, form, change):
        """Save the user, then bring their database account in line with the switch.

        After the user is saved, because the rank enroll() reads comes from the groups
        this save may itself have changed. A database failure is reported and the user
        edit still stands: the switch shows the account that exists, so the next save
        retries rather than leaving the two silently disagreeing.

        Args:
            request: The admin request, for the result message.
            obj: The user being saved.
            form: The submitted form, carrying the switch.
            change: Whether this is an edit rather than a creation.
        """
        super().save_model(request, obj, form, change)

        if "database_access" not in form.fields or form.fields["database_access"].disabled:
            return

        wanted = form.cleaned_data.get("database_access", False)
        # Re-read rather than trusting the form's initial value: the groups saved above
        # may have just changed what the person is entitled to.
        current = hasattr(obj, "database_user")
        if wanted == current:
            return

        try:
            if wanted:
                enroll(obj)
                self.message_user(
                    request,
                    f"Database access enrolled for {obj.username}. Their password is "
                    f"shown to them, once, the next time they sign in here.",
                    messages.SUCCESS,
                )
            else:
                remove(obj)
                self.message_user(
                    request,
                    f"Database access removed for {obj.username}. Anything their role "
                    f"owned was dropped with it.",
                    messages.WARNING,
                )
        except Exception as error:
            logger.exception("Could not change database access for %s", obj.username)
            self.message_user(
                request,
                f"The user was saved, but their database access was not changed: {error}",
                messages.ERROR,
            )


@admin.register(Group)
class GroupAdmin(BaseGroupAdmin, ModelAdmin):
    pass
