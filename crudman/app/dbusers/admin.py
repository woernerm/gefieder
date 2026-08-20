"""Enrolling a person for database access.

Deliberately no password appears on these pages. An administrator decides *that* someone
gets an account; the credential is generated on that person's next sign-in and shown to
them alone (see signals.py). That split is what keeps an administrator from learning a
password that is not theirs, and it means no secret has to be stored while it waits to be
collected -- PostgreSQL holds a SCRAM verifier and this app holds nothing.

A forgotten password is therefore not recovered but reissued: "Reset password" clears the
old one at once and the next sign-in shows a new one.

When the identity provider can authenticate database connections directly (see
backends.py), the backend reports that it issues no secret and the waiting step disappears
without any other change here.
"""
from django.contrib import admin, messages
from django.contrib.auth.models import User
from unfold.admin import ModelAdmin

from .backends import get_backend
from .models import DatabaseUser
from .utils import db_role_for_user, disable, enroll, remove, reset


@admin.register(DatabaseUser)
class DatabaseUserAdmin(ModelAdmin):
    """Database accounts, listed by the person they belong to.

    Rows are not added by hand: an account is provisioned for an existing administrator
    through the action below, because the Django user is the source of truth for who
    exists and what rank they hold.
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
        # role behind, which would look like the account was deleted while its login still
        # worked. delete_selected_accounts below is the one that removes both.
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

        The new password is not shown here: like the first one, it belongs to the account's
        owner and appears on their screen, not on an administrator's.
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

        Offered beside "Disable" rather than instead of it, because the two answer
        different questions. Disabling is the right choice when someone leaves: the role
        stays, so their models and tables keep an owner and remain readable. Dropping is
        for an account created by mistake -- PostgreSQL will not remove a role while it
        still owns anything, so deleting the account deletes that data too.

        Django's own "delete selected" is not reused for this: it would remove the row
        while leaving the PostgreSQL role behind, which is the one outcome that must not
        happen.
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
    already looking when they decide someone needs SQL access.

    No password appears here. The role is created without one and the credential is
    generated on the person's next sign-in, shown to them alone -- so an administrator
    never has to relay a secret they should not have seen, and nothing is stored while it
    waits to be collected.
    """
    backend = get_backend()

    for user in queryset:
        if db_role_for_user(user) is None:
            modeladmin.message_user(
                request,
                f"{user.username} is in none of the roles that grant database access "
                f"(sso-viewer, sso-editor, sso-admin).",
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
