"""Enrolling a person for database access.

Deliberately no password appears on these pages: an administrator decides *that* someone
gets an account, and the credential is generated on that person's next sign-in and shown
to them alone (see signals.py). So a forgotten password is reissued rather than
recovered — "Reset password" clears the old one at once.

When the identity provider can authenticate database connections directly (see
backends.py), the backend reports that it issues no secret and the waiting step
disappears without any other change here.
"""
from django.contrib import admin, messages
from django.contrib.auth.models import User
from unfold.admin import ModelAdmin

from .backends import get_backend
from .models import DatabaseUser
from .utils import GROUP_TO_DB_ROLE, db_role_for_user, disable, enroll, remove, reset


@admin.register(DatabaseUser)
class DatabaseUserAdmin(ModelAdmin):
    """Database accounts, listed by the person they belong to.

    Rows are not added by hand: an account is provisioned through
    ``create_database_user`` below, because the Django user is the source of truth for
    who exists and what rank they hold.
    """

    list_display = (
        "role_name",
        "user",
        "group_role",
        "is_enabled",
        "awaiting_credential",
        "provisioned_on",
    )
    list_filter = ("is_enabled", "awaiting_credential", "group_role")
    search_fields = ("role_name", "user__username", "user__email")
    readonly_fields = (
        "role_name",
        "user",
        "group_role",
        "is_enabled",
        "awaiting_credential",
        "provisioned_on",
    )
    actions = ("disable_selected", "reset_password", "delete_selected_accounts")

    def has_add_permission(self, request):
        # Provisioning starts from the user, not from an empty form: the rank comes from
        # the person's groups and the role name from their username, so there is nothing
        # here for an operator to fill in.
        return False

    def get_actions(self, request):
        # Django's built-in "delete selected" removes the row and leaves the PostgreSQL
        # role behind, so the account would look deleted while its login still worked.
        # delete_selected_accounts below removes both.
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions

    def has_delete_permission(self, request, obj=None):
        # Same reason: the per-object delete button would drop the row only. Deleting an
        # account is the action above.
        return False

    @admin.action(description="Disable database access")
    def disable_selected(self, request, queryset):
        for record in queryset:
            disable(record.role_name)
        self.message_user(
            request,
            f"Disabled {queryset.count()} database user(s). They keep everything they "
            f"own but can no longer connect.",
            messages.SUCCESS,
        )

    @admin.action(description="Reset password")
    def reset_password(self, request, queryset):
        """Clear the password so a new one is issued on the person's next sign-in.

        The new password is not shown here: like the first one, it appears on the
        account owner's screen, not on an administrator's.

        Args:
            request: The admin request, for the result message.
            queryset: The selected database users.
        """
        backend = get_backend()
        if not backend.issues_secret:
            self.message_user(
                request,
                f"{backend.name} needs no password: these accounts authenticate against "
                f"the identity provider.",
                messages.WARNING,
            )
            return

        for record in queryset:
            reset(record.user)

        self.message_user(
            request,
            f"Cleared the password of {queryset.count()} database user(s). They cannot "
            f"connect until they sign in here, where a new password is shown to them once.",
            messages.SUCCESS,
        )

    @admin.action(description="Delete database account (destroys owned data)")
    def delete_selected_accounts(self, request, queryset):
        """Drop the role outright, with whatever it owns.

        Offered beside "Disable" because the two answer different questions. Disabling
        suits a departure: the role stays, so their tables keep an owner. Dropping is for
        an account created by mistake — PostgreSQL will not remove a role while it still
        owns anything, so deleting the account deletes that data too.

        Args:
            request: The admin request, for the result message.
            queryset: The selected database users.
        """
        names = [record.role_name for record in queryset]

        for record in list(queryset):
            remove(record.user)

        self.message_user(
            request,
            f"Deleted {len(names)} database account(s): {', '.join(names)}. Anything they "
            f"owned was dropped with them.",
            messages.WARNING,
        )


@admin.action(description="Create database account")
def create_database_user(modeladmin, request, queryset):
    """Enroll the selected administrators for database access.

    Lives on the user admin rather than this app's, because that is where an operator is
    already looking when they decide someone needs SQL access. The role is created
    without a password; the credential is generated on the person's next sign-in.

    Args:
        modeladmin: The user admin the action is attached to, for its messages.
        request: The admin request.
        queryset: The selected Django users.
    """
    backend = get_backend()

    for user in queryset:
        if db_role_for_user(user) is None:
            modeladmin.message_user(
                request,
                f"{user.username} is in none of the roles that grant database access "
                f"({', '.join(GROUP_TO_DB_ROLE)}).",
                messages.ERROR,
            )
            continue

        enroll(user)

        if backend.issues_secret:
            modeladmin.message_user(
                request,
                f"Database account enrolled for {user.username}. Their password is shown "
                f"to them, once, the next time they sign in here.",
                messages.SUCCESS,
            )
        else:
            modeladmin.message_user(
                request,
                f"Database account created for {user.username}. "
                f"{backend.connection_hint(user.username)}",
                messages.SUCCESS,
            )


# The action is attached to the existing user admin, which sso/admin.py registered.
admin.site._registry[User].actions = list(
    admin.site._registry[User].actions or ()
) + [create_database_user]
