"""One menu section for who may sign in and what they may do.

Users and groups are Django's, administered here whether single sign-on is on or off.
allauth registers four more pages under two further headings: three are dead weight, the
fourth comes back as an inline on the user it describes.

Database access is a switch on this page rather than a section of its own, being one more
thing a person either has or does not, alongside active and staff status.
"""
import logging

from dbusers.utils import db_role_for_user, enroll, remove, unmanaged_role
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
    # unregistered, and autodiscovery reaches allauth only after this module.
    import allauth.account.admin  # noqa: F401
    import allauth.socialaccount.admin  # noqa: F401
    from allauth.account.models import EmailAddress
    from allauth.socialaccount.models import SocialAccount, SocialApp, SocialToken

    # SocialApp: the provider is configured in settings.py, so a row here would be a
    #   second and conflicting source. SocialToken: SOCIALACCOUNT_STORE_TOKENS is off.
    # EmailAddress: no address is confirmed here, and the user already carries it.
    # SocialAccount: kept, as the inline below rather than as a list beside the users.
    for model in (EmailAddress, SocialApp, SocialToken, SocialAccount):
        admin.site.unregister(model)

    class SingleSignOnInline(TabularInline):
        """The directory account behind a user, on the user rather than beside them.

        Answers "the provider says I am an admin, so why am I not?" from extra_data, the
        claims exactly as they arrived. Read only: every field is rewritten on the next
        login, and removing the row would strand the account.
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

    Not Django's boolean filter: the flag is not a field but the presence of the related
    dbusers row.
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

    Not a model field: the account lives in PostgreSQL, so the switch reports what exists
    rather than storing an intention, and the save reconciles the two.
    """

    # Unfold's formfield_overrides reach model fields only, so the widget that makes this
    # look like is_staff beside it has to be named here.
    database_access = forms.BooleanField(
        label="Database access",
        required=False,
        widget=UnfoldBooleanSwitchWidget,
        help_text=(
            "Designates whether the user gets a PostgreSQL login role. "
            "A random password is shown to them, once, the next time they sign in."
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        user = self.instance
        self.fields["database_access"].initial = (
            user.pk is not None and hasattr(user, "database_user")
        )

        if user.pk is None:
            return

        # A role of that name may exist without being ours -- the deployment's superuser
        # is the plain case. The person reaches the database through it, so the switch is
        # on, and read-only because the provisioning functions refuse a foreign role.
        existing = unmanaged_role(user)
        if existing:
            self.fields["database_access"].initial = True
            self.fields["database_access"].disabled = True
            self.fields["database_access"].help_text = (
                f'User "{existing}" is the database superuser. It cannot be removed.'
            )
            return

        # The password is handed over at the next sign-in here, so someone who cannot
        # reach the admin would never learn it. Staff status first, saved, then this.
        if not user.is_staff:
            self.fields["database_access"].disabled = True
            self.fields["database_access"].help_text = (
                "The user needs staff status first."
            )
            return

        # Without a rank there is nothing to grant and enroll() would refuse; saying so
        # beats failing on save. A superuser is exempt: with single sign-on off nobody
        # carries a role group, and the local administrator still needs an account.
        if db_role_for_user(user) is None and not user.is_superuser:
            self.fields["database_access"].disabled = True
            self.fields["database_access"].help_text = (
                "This user has insufficient privileges."
            )


admin.site.unregister(User)
admin.site.unregister(Group)


@admin.register(User)
class UserAdmin(BaseUserAdmin, ModelAdmin):
    form = UserWithDatabaseAccessForm
    add_form = UserCreationForm
    change_password_form = AdminPasswordChangeForm

    # Stamped by Django on login and on creation; an edit would only make them lie.
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

    # Without single sign-on the inline's model does not exist.
    inlines = [SingleSignOnInline] if settings.OIDC_ENABLED else []

    def get_queryset(self, request):
        # The column and the filter both ask about the related row.
        return super().get_queryset(request).select_related("database_user")

    def get_inlines(self, request, obj=None):
        # Someone created here has signed in nowhere yet, so the add page would carry an
        # empty box and then insist on its formset before saving.
        return super().get_inlines(request, obj) if obj else []

    @admin.display(description="database access", boolean=True)
    def has_database_access(self, obj):
        return hasattr(obj, "database_user")

    def save_model(self, request, obj, form, change):
        """Save the user, then bring their database account in line with the switch.

        After the user is saved, because the rank enroll() reads comes from the groups
        this save may have changed. A database failure is reported and the user edit still
        stands, so the next save retries.

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
        # may have changed what the person is entitled to.
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
