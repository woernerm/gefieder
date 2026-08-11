from unittest import skipUnless

from django.conf import settings
from django.contrib.auth.models import Group, Permission, User
from django.core.exceptions import PermissionDenied
from django.test import TestCase, override_settings
from django.urls import reverse

from .roles import (
    GROUP_ACTIONS,
    apply_roles,
    claimed_roles,
    create_role_groups,
    highest_role,
)


class RoleGroupTests(TestCase):
    """The three groups exist after a migration, with permissions to start from."""

    def test_the_groups_are_created(self):
        # post_migrate has already run for the test database.
        self.assertQuerySetEqual(
            Group.objects.filter(name__in=GROUP_ACTIONS).order_by("name"),
            ["sso-admin", "sso-editor", "sso-viewer"],
            transform=str,
        )

    def test_a_viewer_may_only_view(self):
        actions = {
            perm.codename.split("_")[0]
            for perm in Group.objects.get(name="sso-viewer").permissions.all()
        }
        self.assertEqual(actions, {"view"})

    def test_only_the_admin_role_may_delete(self):
        for name, actions in GROUP_ACTIONS.items():
            granted = {
                perm.codename.split("_")[0]
                for perm in Group.objects.get(name=name).permissions.all()
            }
            self.assertEqual("delete" in granted, "delete" in actions, name)

    def test_no_role_may_touch_users_or_groups(self):
        # An editor who can add users could grant themselves anything, so the groups are
        # confined to this project's own apps.
        for name in GROUP_ACTIONS:
            labels = {
                perm.content_type.app_label
                for perm in Group.objects.get(name=name).permissions.all()
            }
            self.assertNotIn("auth", labels, name)

    def test_permissions_of_an_existing_group_are_left_alone(self):
        # Re-running post_migrate must not undo an operator's edits, or every deployment
        # would silently reset the permission sets they tuned.
        group = Group.objects.get(name="sso-viewer")
        group.permissions.set([Permission.objects.first()])

        create_role_groups()

        self.assertEqual(list(group.permissions.all()), [Permission.objects.first()])


class ClaimedRolesTests(TestCase):
    """Finding the roles in what the provider returned."""

    def test_roles_are_read_from_the_id_token(self):
        # Where Entra ID puts app roles.
        data = {"id_token": {"roles": ["Editor"]}, "userinfo": {"sub": "abc"}}

        self.assertEqual(claimed_roles(data), ["Editor"])

    def test_the_id_token_wins_over_userinfo(self):
        # Entra ID's userinfo endpoint returns no roles at all, and allauth prefers
        # userinfo when both are present, so reading its merged view would find nothing.
        data = {"id_token": {"roles": ["Admin"]}, "userinfo": {"roles": ["Viewer"]}}

        self.assertEqual(claimed_roles(data), ["Admin"])

    def test_userinfo_is_used_when_the_token_has_no_roles(self):
        # Providers that put the claim on the userinfo endpoint instead.
        data = {"id_token": {"sub": "abc"}, "userinfo": {"roles": ["Viewer"]}}

        self.assertEqual(claimed_roles(data), ["Viewer"])

    def test_a_flat_payload_is_understood(self):
        self.assertEqual(claimed_roles({"roles": ["Viewer"]}), ["Viewer"])

    def test_nothing_is_found_when_the_claim_is_absent(self):
        self.assertEqual(claimed_roles({"id_token": {"sub": "abc"}}), [])
        self.assertEqual(claimed_roles({}), [])
        self.assertEqual(claimed_roles(None), [])


class HighestRoleTests(TestCase):
    """The claim is a list, and someone may hold several roles at once."""

    def test_a_known_role_maps_to_its_group(self):
        self.assertEqual(highest_role(["Editor"]), "sso-editor")

    def test_the_most_privileged_role_wins(self):
        self.assertEqual(highest_role(["Viewer", "Admin", "Editor"]), "sso-admin")

    def test_matching_ignores_capitalisation(self):
        # Providers differ on how they spell the role back to us.
        self.assertEqual(highest_role(["ADMIN"]), "sso-admin")

    def test_unknown_roles_grant_nothing(self):
        self.assertIsNone(highest_role(["Sales", "Marketing"]))

    def test_an_absent_claim_grants_nothing(self):
        self.assertIsNone(highest_role(None))
        self.assertIsNone(highest_role([]))


class ApplyRolesTests(TestCase):
    """Applying a login's roles: the provider owns the flags and the sso- groups only."""

    def setUp(self):
        self.user = User.objects.create_user("kim")

    def test_the_role_group_is_granted(self):
        apply_roles(self.user, ["Editor"])
        self.assertEqual(
            list(self.user.groups.values_list("name", flat=True)), ["sso-editor"]
        )

    def test_a_changed_role_replaces_the_previous_one(self):
        apply_roles(self.user, ["Viewer"])
        apply_roles(self.user, ["Editor"])
        self.assertEqual(
            list(self.user.groups.values_list("name", flat=True)), ["sso-editor"]
        )

    def test_groups_granted_by_hand_survive_a_login(self):
        # The point of the whole scheme: a baseline from the provider, extras assigned
        # locally on top. Rewriting the group list instead of reconciling the managed ones
        # would delete this membership on the user's next login.
        local = Group.objects.create(name="project-b-analysts")
        self.user.groups.add(local)

        apply_roles(self.user, ["Viewer"])

        self.assertEqual(
            sorted(self.user.groups.values_list("name", flat=True)),
            ["project-b-analysts", "sso-viewer"],
        )

    def test_losing_the_role_deactivates_the_account(self):
        apply_roles(self.user, ["Admin"])

        apply_roles(self.user, [])

        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)
        self.assertFalse(self.user.is_staff)
        self.assertFalse(self.user.is_superuser)
        self.assertEqual(list(self.user.groups.all()), [])

    def test_losing_the_role_keeps_local_groups(self):
        # Deactivating is not the same as stripping: if the person comes back, the rights
        # someone granted them by hand should still be there.
        local = Group.objects.create(name="project-b-analysts")
        self.user.groups.add(local)

        apply_roles(self.user, [])

        self.assertEqual(
            list(self.user.groups.values_list("name", flat=True)), ["project-b-analysts"]
        )

    def test_the_admin_role_grants_superuser(self):
        apply_roles(self.user, ["Admin"])

        self.user.refresh_from_db()
        self.assertTrue(self.user.is_superuser)
        self.assertTrue(self.user.is_staff)

    def test_dropping_from_admin_to_editor_withdraws_superuser(self):
        # is_superuser bypasses every permission check, so it has to be taken away by the
        # same login that takes the role away.
        apply_roles(self.user, ["Admin"])

        apply_roles(self.user, ["Editor"])

        self.user.refresh_from_db()
        self.assertFalse(self.user.is_superuser)
        self.assertTrue(self.user.is_staff)

    def test_a_role_opens_the_admin(self):
        apply_roles(self.user, ["Viewer"])

        self.user.refresh_from_db()
        self.assertTrue(self.user.is_staff)
        self.assertTrue(self.user.is_active)

    def test_the_granted_group_is_returned(self):
        self.assertEqual(apply_roles(self.user, ["Viewer"]), "sso-viewer")
        self.assertIsNone(apply_roles(self.user, ["Nothing"]))


class FakeSocialLogin:
    """Enough of allauth's SocialLogin for the adapter to work on.

    The adapter reads two things off it -- the account's claims and whether the user
    already exists -- so standing those in keeps these tests about the mapping rather than
    about allauth's own machinery. The exchange that produces the claims is covered end to
    end by the integration suite, against a real OpenID Connect server.
    """

    def __init__(self, user, claims, is_existing=True):
        self.user = user
        self.is_existing = is_existing
        self.account = type("FakeAccount", (), {"extra_data": {"id_token": claims}})()


@skipUnless(settings.OIDC_ENABLED, "allauth is only installed when single sign-on is on")
class AdapterTests(TestCase):
    """The allauth hook: who is let in, and what they are granted on the way."""

    def setUp(self):
        from .adapters import SSOAccountAdapter

        self.adapter = SSOAccountAdapter()
        self.user = User.objects.create_user("kim")

    def test_an_existing_account_is_reconciled_before_the_session_starts(self):
        # Not on the next login but on this one, so a role taken away in the directory
        # stops working immediately.
        login = FakeSocialLogin(self.user, {"roles": ["Editor"]})

        self.adapter.pre_social_login(None, login)

        self.assertEqual(
            list(self.user.groups.values_list("name", flat=True)), ["sso-editor"]
        )

    def test_someone_without_a_role_is_refused(self):
        # Authenticating is not the same as having been granted access.
        login = FakeSocialLogin(self.user, {"roles": ["Sales"]})

        with self.assertRaises(PermissionDenied):
            self.adapter.pre_social_login(None, login)

    def test_a_refused_login_changes_nothing(self):
        apply_roles(self.user, ["Admin"])
        login = FakeSocialLogin(self.user, {})

        with self.assertRaises(PermissionDenied):
            self.adapter.pre_social_login(None, login)

        # The refusal comes before any change, so the account is left as it was rather
        # than half-updated by a login that did not complete.
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_superuser)

    def test_a_new_account_is_not_touched_before_it_exists(self):
        # A user allauth has not saved yet has no primary key, so group membership cannot
        # be written; save_user does it once the row is there.
        login = FakeSocialLogin(User(username="new"), {"roles": ["Viewer"]}, is_existing=False)

        self.adapter.pre_social_login(None, login)  # must not raise

    def test_a_role_taken_away_deactivates_an_existing_account(self):
        apply_roles(self.user, ["Editor"])
        login = FakeSocialLogin(self.user, {"roles": ["Viewer"]})

        self.adapter.pre_social_login(None, login)

        self.user.refresh_from_db()
        self.assertEqual(
            list(self.user.groups.values_list("name", flat=True)), ["sso-viewer"]
        )


@skipUnless(settings.OIDC_ENABLED, "the provider's routes exist only when it is on")
class ProviderRoutesTests(TestCase):
    """With single sign-on on, the routes the provider redirects back to are mounted."""

    def test_the_callback_address_is_served(self):
        # The address registered with the identity provider. Reversing it here is what
        # catches a change to the URL layout that would silently invalidate that
        # registration and break every sign-in.
        self.assertEqual(
            reverse("openid_connect_callback", kwargs={"provider_id": "sso"}),
            "/crudman/accounts/oidc/sso/login/callback/",
        )

    def test_the_callback_is_served_by_allauth_and_not_the_admin(self):
        # The admin's URLs end in a catch-all under the same prefix, so whichever is
        # listed first wins. With the admin first, the callback would redirect anonymous
        # visitors to the login page, which redirects to the provider, which comes back
        # here -- a loop that ends only when the browser gives up.
        from django.urls import resolve

        match = resolve("/crudman/accounts/oidc/sso/login/callback/")

        self.assertTrue(
            match.func.__module__.startswith("allauth."),
            f"the callback is served by {match.func.__module__}, not allauth",
        )

    def test_the_login_page_redirects_to_the_real_provider_route(self):
        # The same assertion the stubbed URLconf makes below, but against the routes
        # allauth actually mounts.
        response = self.client.get(reverse("login"))

        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/oidc/sso/login/", response.url)


class LoginRedirectTests(TestCase):
    """The admin's login address, which stands in front of the local form."""

    @override_settings(OIDC_ENABLED=False)
    def test_the_local_form_is_served_when_sso_is_off(self):
        # Pinned rather than inherited, so the assertion holds in both runs of this suite.
        response = self.client.get(reverse("login"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "csrfmiddlewaretoken")

    @override_settings(OIDC_ENABLED=True, ROOT_URLCONF="sso.test_urls")
    def test_a_visitor_is_sent_to_the_provider(self):
        response = self.client.get(reverse("login"))

        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/oidc/sso/login/", response.url)

    @override_settings(OIDC_ENABLED=True, ROOT_URLCONF="sso.test_urls")
    def test_the_original_destination_is_carried_along(self):
        response = self.client.get(reverse("login"), {"next": "/crudman/tenants/"})

        self.assertIn("next=%2Fcrudman%2Ftenants%2F", response.url)

    @override_settings(OIDC_ENABLED=True, ROOT_URLCONF="sso.test_urls")
    def test_the_escape_parameter_reaches_the_local_form(self):
        # The way back in for the superuser when the provider is misconfigured.
        response = self.client.get(reverse("login"), {"local": "1"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "csrfmiddlewaretoken")

    @override_settings(OIDC_ENABLED=True, ROOT_URLCONF="sso.test_urls")
    def test_the_local_form_can_still_be_submitted(self):
        # The form posts back without the query string, so a POST has to count as local or
        # the escape hatch would bounce the submission to the provider instead.
        User.objects.create_superuser("root", password="hunter2hunter2")

        response = self.client.post(
            reverse("login"), {"username": "root", "password": "hunter2hunter2"}
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(int(self.client.session["_auth_user_id"]), User.objects.get(username="root").pk)


PROVIDER_LOGOUT = "https://provider.example.com/logout"


class LogoutTests(TestCase):
    """Signing out, which has to end the provider's session as well as this one."""

    def setUp(self):
        self.user = User.objects.create_superuser("root", password="hunter2hunter2")
        self.client.force_login(self.user)

    @override_settings(OIDC_ENABLED=False)
    def test_the_admin_handles_it_when_sso_is_off(self):
        # Nothing to sign out of elsewhere, so the admin's own page is the right answer.
        response = self.client.post(reverse("logout"))

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    @override_settings(OIDC_ENABLED=True, OIDC_LOGOUT_URL=PROVIDER_LOGOUT)
    def test_the_browser_is_sent_on_to_the_provider(self):
        response = self.client.post(reverse("logout"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, PROVIDER_LOGOUT)

    @override_settings(OIDC_ENABLED=True, OIDC_LOGOUT_URL=PROVIDER_LOGOUT)
    def test_the_session_ends_here_first(self):
        # Ending it locally must not wait on the provider answering: if the redirect is
        # never followed, the person still has to be signed out of this system.
        self.client.post(reverse("logout"))

        self.assertNotIn("_auth_user_id", self.client.session)

    @override_settings(OIDC_ENABLED=True, OIDC_LOGOUT_URL="")
    def test_the_admin_handles_it_when_no_logout_address_is_configured(self):
        # Single sign-on without an end-session address: the local session still ends, and
        # there is nowhere to forward to.
        response = self.client.post(reverse("logout"))

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    @override_settings(OIDC_ENABLED=True, OIDC_LOGOUT_URL=PROVIDER_LOGOUT)
    def test_a_link_cannot_sign_someone_out(self):
        # Django stopped accepting logout from a GET so that another site cannot trigger
        # it, and routing it through the provider must not reopen that.
        response = self.client.get(reverse("logout"))

        self.assertEqual(response.status_code, 405)
        self.assertIn("_auth_user_id", self.client.session)
