from base64 import b64decode, b64encode
from logging import CRITICAL, disable
from unittest import skipUnless
from unittest.mock import MagicMock, patch

import requests
from dbusers.models import DatabaseUser
from dbusers.utils import enroll
from django.conf import settings
from django.contrib import admin
from django.contrib.auth import authenticate
from django.contrib.auth.models import Group, Permission, User
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils.functional import SimpleLazyObject

from .avatars import (
    MAX_PICTURE_BYTES,
    SESSION_KEY,
    SESSION_PICTURE_KEY,
    api_host,
    claimed_picture,
    fetched_picture,
    loadable_picture,
    middleware,
    remember_picture,
)
from .roles import (
    GROUP_ACTIONS,
    GROUP_FOR_RANK,
    apply_roles,
    claimed_roles,
    create_role_groups,
    highest_role,
)
from .scopes import scopes_for
from unfold.widgets import UnfoldBooleanSwitchWidget

# The group each rank grants, built the way roles.py builds it rather than spelled out, so
# these tests follow SSO_GROUP_PREFIX instead of asserting the value it ships with.
VIEWER = GROUP_FOR_RANK["viewer"]
EDITOR = GROUP_FOR_RANK["editor"]
ADMIN = GROUP_FOR_RANK["admin"]


class RoleGroupTests(TestCase):
    """The three groups exist after a migration, with permissions to start from."""

    def test_the_groups_are_created(self):
        # post_migrate has already run for the test database.
        self.assertQuerySetEqual(
            Group.objects.filter(name__in=GROUP_ACTIONS).order_by("name"),
            sorted([ADMIN, EDITOR, VIEWER]),
            transform=str,
        )

    def test_a_viewer_may_only_view(self):
        actions = {
            perm.codename.split("_")[0]
            for perm in Group.objects.get(name=VIEWER).permissions.all()
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
        group = Group.objects.get(name=VIEWER)
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
        self.assertEqual(highest_role(["Editor"]), EDITOR)

    def test_the_most_privileged_role_wins(self):
        self.assertEqual(highest_role(["Viewer", "Admin", "Editor"]), ADMIN)

    def test_matching_ignores_capitalisation(self):
        # Providers differ on how they spell the role back to us.
        self.assertEqual(highest_role(["ADMIN"]), ADMIN)

    def test_unknown_roles_grant_nothing(self):
        self.assertIsNone(highest_role(["Sales", "Marketing"]))

    def test_an_absent_claim_grants_nothing(self):
        self.assertIsNone(highest_role(None))
        self.assertIsNone(highest_role([]))


class ApplyRolesTests(TestCase):
    """Applying a login's roles: the provider owns the flags and the rank groups only."""

    def setUp(self):
        self.user = User.objects.create_user("kim")

    def test_the_role_group_is_granted(self):
        apply_roles(self.user, ["Editor"])
        self.assertEqual(
            list(self.user.groups.values_list("name", flat=True)), [EDITOR]
        )

    def test_a_changed_role_replaces_the_previous_one(self):
        apply_roles(self.user, ["Viewer"])
        apply_roles(self.user, ["Editor"])
        self.assertEqual(
            list(self.user.groups.values_list("name", flat=True)), [EDITOR]
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
            sorted(["project-b-analysts", VIEWER]),
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
        self.assertEqual(apply_roles(self.user, ["Viewer"]), VIEWER)
        self.assertIsNone(apply_roles(self.user, ["Nothing"]))


class FakeSocialLogin:
    """Enough of allauth's SocialLogin for the adapter to work on.

    The adapter reads two things off it — the account's claims and whether the user
    already exists — so standing those in keeps these tests about the mapping rather than
    about allauth's own machinery. The exchange that produces the claims is covered end to
    end by the integration suite, against a real OpenID Connect server.
    """

    def __init__(self, user, claims, is_existing=True, token=None):
        self.user = user
        self.is_existing = is_existing
        self.account = type("FakeAccount", (), {"extra_data": {"id_token": claims}})()
        # The access token allauth holds during a login and, here, never stores. Absent
        # unless a test is about the one thing that uses it: the profile picture a
        # provider will hand to this server but not to the browser.
        self.token = type("FakeToken", (), {"token": token})() if token else None


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
            list(self.user.groups.values_list("name", flat=True)), [EDITOR]
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
            list(self.user.groups.values_list("name", flat=True)), [VIEWER]
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
        # here — a loop that ends only when the browser gives up.
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


class AdminMenuTests(TestCase):
    """The sidebar, which says the same thing whichever way people sign in."""

    def setUp(self):
        request = RequestFactory().get(reverse("admin:index"))
        request.user = User.objects.create_superuser("root")

        # What the sidebar is built from, seen by a superuser, so nothing is missing merely
        # for want of a permission.
        self.sections = {
            app["name"]: [model["name"] for model in app["models"]]
            for app in admin.site.get_app_list(request)
        }

    def test_one_heading_covers_signing_in(self):
        self.assertIn("Access", self.sections)

        # Django's own name for the section, and the two allauth arrives with. With single
        # sign-on on, all three stood in the menu at once, saying much the same thing.
        for heading in ("Authentication and Authorization", "Accounts", "Social Accounts"):
            self.assertNotIn(heading, self.sections)

    def test_the_heading_holds_users_and_groups_only(self):
        self.assertEqual(self.sections["Access"], ["Groups", "Users"])

    def test_database_access_has_no_section_of_its_own(self):
        """It is a switch on the user, so a heading listing the accounts would be a
        second place to look for the same fact."""
        self.assertNotIn("Database access", self.sections)


@skipUnless(settings.OIDC_ENABLED, "allauth is only installed when single sign-on is on")
class AllauthAdminPageTests(TestCase):
    """The pages allauth registers, none of which earns a place in this system's menu."""

    def test_they_are_unregistered_rather_than_hidden(self):
        from allauth.account.models import EmailAddress
        from allauth.socialaccount.models import SocialAccount, SocialApp, SocialToken

        # Dropping them from the sidebar alone would not do: a page that stays registered
        # stays reachable by typing its address, and the social application one would then
        # offer to configure a provider that is really configured in settings.py.
        for model in (EmailAddress, SocialApp, SocialToken, SocialAccount):
            self.assertFalse(admin.site.is_registered(model), model.__name__)


@skipUnless(settings.OIDC_ENABLED, "the inline's model ships with allauth")
class SingleSignOnInlineTests(TestCase):
    """The provider's account, kept as part of the user instead of as a menu entry."""

    def setUp(self):
        from allauth.socialaccount.models import SocialAccount

        self.user = User.objects.create_user("kim")
        SocialAccount.objects.create(
            user=self.user,
            provider=settings.OIDC_PROVIDER_ID,
            uid="kim@example.com",
            extra_data={"id_token": {"roles": ["Editor"]}},
        )
        self.client.force_login(User.objects.create_superuser("root"))
        self.page = self.client.get(
            reverse("admin:auth_user_change", args=[self.user.pk])
        )

    def test_the_claims_are_shown_on_the_user(self):
        # The question this page is here to answer: the provider calls someone an editor
        # and the system does not, so show the roles exactly as they arrived.
        self.assertContains(self.page, "kim@example.com")
        self.assertContains(self.page, "Editor")

    def test_nothing_of_it_can_be_edited(self):
        # Read-only fields render as text, so a form input for one would mean the
        # provider's data had become editable here — and rewritten on its next login.
        self.assertNotContains(self.page, "socialaccount_set-0-uid")

    def test_no_account_can_be_added_by_hand(self):
        from .admin import SingleSignOnInline

        inline = SingleSignOnInline(User, admin.site)

        self.assertFalse(inline.has_add_permission(self.page.wsgi_request, self.user))


class LocalUserCreationTests(TestCase):
    """Making a user by hand.

    The whole story with single sign-on off, and how the local superuser goes on
    existing when it is on."""

    def setUp(self):
        self.client.force_login(User.objects.create_superuser("root"))

    def test_the_add_page_asks_for_a_password(self):
        response = self.client.get(reverse("admin:auth_user_add"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="password1"')

    def test_the_add_page_says_nothing_about_the_provider(self):
        # Someone created here has signed in nowhere yet, so the box would be empty — and
        # the page would refuse to save the new user without its formset coming back.
        response = self.client.get(reverse("admin:auth_user_add"))

        self.assertNotContains(response, "socialaccount_set-TOTAL_FORMS")

    def test_a_user_created_here_can_sign_in(self):
        response = self.client.post(
            reverse("admin:auth_user_add"),
            {
                "username": "kim",
                "password1": "hunter2hunter2",
                "password2": "hunter2hunter2",
            },
        )

        # The add page redirects to the new user's own page; a 200 would be the form
        # coming back with errors.
        self.assertEqual(response.status_code, 302)
        self.assertIsNotNone(authenticate(username="kim", password="hunter2hunter2"))

    @override_settings(OIDC_ENABLED=True, OIDC_LOGOUT_URL=PROVIDER_LOGOUT)
    def test_a_link_cannot_sign_someone_out(self):
        # Django stopped accepting logout from a GET so that another site cannot trigger
        # it, and routing it through the provider must not reopen that.
        response = self.client.get(reverse("logout"))

        self.assertEqual(response.status_code, 405)
        self.assertIn("_auth_user_id", self.client.session)


# A picture a browser can fetch, as a standards-abiding provider publishes it, and the one
# Entra ID publishes instead: an address inside Microsoft Graph that answers only to a
# request carrying a bearer token.
PICTURE = "https://provider.example/photos/kim.jpg"
GRAPH_PICTURE = "https://graph.microsoft.com/v1.0/me/photo/$value"


PHOTO = b"the bytes of a photograph"


def fake_answer(status=200, content_type="image/jpeg", body=PHOTO):
    """Stand in for what requests.get returns, context manager and all."""
    answer = MagicMock()
    answer.ok = status < 400
    answer.headers = {"content-type": content_type}
    answer.raw.read.return_value = body
    answer.__enter__.return_value = answer
    return answer


class ClaimedPictureTests(TestCase):
    """Finding the picture in what the provider returned."""

    def test_the_picture_is_read_from_userinfo(self):
        data = {"userinfo": {"picture": PICTURE}, "id_token": {"sub": "kim"}}

        self.assertEqual(claimed_picture(data), PICTURE)

    def test_userinfo_wins_over_the_id_token(self):
        # The opposite of the roles, and for the same reason: each is taken from where its
        # provider actually puts it.
        data = {
            "userinfo": {"picture": PICTURE},
            "id_token": {"picture": "https://provider.example/photos/stale.jpg"},
        }

        self.assertEqual(claimed_picture(data), PICTURE)

    def test_the_id_token_is_used_when_userinfo_has_none(self):
        data = {"userinfo": {"sub": "kim"}, "id_token": {"picture": PICTURE}}

        self.assertEqual(claimed_picture(data), PICTURE)

    def test_a_flat_payload_is_understood(self):
        self.assertEqual(claimed_picture({"picture": PICTURE}), PICTURE)

    def test_nothing_is_found_when_the_claim_is_absent(self):
        # Most providers publish no picture at all, which is not an error: the initial of
        # the name is what the sidebar has always shown.
        self.assertIsNone(claimed_picture({"userinfo": {"sub": "kim"}}))
        self.assertIsNone(claimed_picture(None))


class LoadablePictureTests(TestCase):
    """Whether the link the provider sent is one that can be displayed."""

    def test_an_image_is_kept(self):
        with patch("sso.avatars.requests.get", return_value=fake_answer()) as get:
            self.assertEqual(loadable_picture(PICTURE), PICTURE)

        # Streamed, because the answer's headers settle it and the picture behind them can
        # be megabytes that nothing here will ever look at.
        self.assertTrue(get.call_args.kwargs["stream"])

    def test_a_link_that_needs_a_token_is_dropped(self):
        # What Entra ID sends. Keeping it would put an address in the stylesheet that the
        # browser is refused, leaving an empty circle where the initial used to be.
        answer = fake_answer(status=401, content_type="application/json")

        with patch("sso.avatars.requests.get", return_value=answer):
            self.assertIsNone(loadable_picture(GRAPH_PICTURE))

    def test_something_that_is_not_an_image_is_dropped(self):
        # A sign-in page, say, which is how some providers answer a request they will not
        # serve — with 200 and a page rather than an error.
        answer = fake_answer(content_type="text/html; charset=utf-8")

        with patch("sso.avatars.requests.get", return_value=answer):
            self.assertIsNone(loadable_picture(PICTURE))

    def test_an_unreachable_host_costs_the_picture_and_nothing_else(self):
        # The login has already succeeded by this point, so a picture host that is down,
        # slow or unresolvable must not turn a completed sign-in into an error page.
        with patch("sso.avatars.requests.get", side_effect=requests.RequestException):
            self.assertIsNone(loadable_picture(PICTURE))

    def test_no_picture_asks_nobody(self):
        with patch("sso.avatars.requests.get") as get:
            self.assertIsNone(loadable_picture(None))

        get.assert_not_called()


class FetchedPictureTests(TestCase):
    """Downloading the picture that the browser is not allowed to have."""

    def test_the_picture_is_downloaded_with_the_token(self):
        with patch("sso.avatars.requests.get", return_value=fake_answer()) as get:
            picture = fetched_picture(GRAPH_PICTURE, "token-123", "graph.microsoft.com")

        self.assertEqual(b64decode(picture["data"]), PHOTO)
        self.assertEqual(picture["content_type"], "image/jpeg")
        self.assertEqual(
            get.call_args.kwargs["headers"], {"Authorization": "Bearer token-123"}
        )

    def test_the_token_is_sent_nowhere_but_the_provider(self):
        # The host comes out of a claim. A tampered or mistaken one must not be able to
        # turn a login into an access token handed to a stranger.
        with patch("sso.avatars.requests.get") as get:
            picture = fetched_picture(
                "https://elsewhere.example/kim.jpg", "token-123", "graph.microsoft.com"
            )

        self.assertIsNone(picture)
        get.assert_not_called()

    def test_nothing_is_asked_when_the_provider_named_no_api(self):
        with patch("sso.avatars.requests.get") as get:
            picture = fetched_picture(GRAPH_PICTURE, "token-123", None)

        self.assertIsNone(picture)
        get.assert_not_called()

    def test_a_picture_too_large_to_carry_is_dropped(self):
        # It would ride in the session row, which Django reads back on every request.
        answer = fake_answer(body=b"x" * (MAX_PICTURE_BYTES + 1))

        with patch("sso.avatars.requests.get", return_value=answer):
            self.assertIsNone(
                fetched_picture(GRAPH_PICTURE, "token-123", "graph.microsoft.com")
            )

    def test_a_refusal_is_dropped(self):
        # A token without the permission the provider wants for photographs, which is a
        # matter of the operator's registration and not something to fail a login over.
        answer = fake_answer(status=403, content_type="application/json")

        with patch("sso.avatars.requests.get", return_value=answer):
            self.assertIsNone(
                fetched_picture(GRAPH_PICTURE, "token-123", "graph.microsoft.com")
            )

    def test_an_unreachable_api_is_dropped(self):
        with patch("sso.avatars.requests.get", side_effect=requests.RequestException):
            self.assertIsNone(
                fetched_picture(GRAPH_PICTURE, "token-123", "graph.microsoft.com")
            )


class ApiHostTests(TestCase):
    """The one host a token may be sent to, read from the provider's own document."""

    def test_the_userinfo_host_is_where_the_token_may_go(self):
        # Where allauth has just sent this very token for the claims themselves, so the
        # picture costs no new trust.
        login = MagicMock()
        adapter = login.account.get_provider.return_value.get_oauth2_adapter.return_value
        adapter.openid_config = {
            "userinfo_endpoint": "https://graph.microsoft.com/oidc/userinfo"
        }

        self.assertEqual(api_host(None, login), "graph.microsoft.com")

    def test_a_provider_that_cannot_be_asked_names_no_host(self):
        # Discovery is a network call like any other, and failing it costs the picture.
        login = MagicMock()
        login.account.get_provider.side_effect = requests.RequestException

        self.assertIsNone(api_host(None, login))


class RememberPictureTests(TestCase):
    """What a login leaves behind for the pages that follow it."""

    def setUp(self):
        self.request = RequestFactory().get("/")
        self.request.session = {}

    def test_the_picture_is_stored_on_the_session(self):
        login = FakeSocialLogin(User(username="kim"), {"picture": PICTURE})

        with patch("sso.avatars.requests.get", return_value=fake_answer()):
            remember_picture(self.request, sociallogin=login)

        self.assertEqual(self.request.session[SESSION_KEY], PICTURE)

    def test_a_login_without_a_provider_clears_it(self):
        self.request.session[SESSION_KEY] = PICTURE

        remember_picture(self.request)

        # Signing in as somebody else must not leave their predecessor's face in the
        # corner of the page.
        self.assertIsNone(self.request.session[SESSION_KEY])

    def test_a_picture_the_browser_may_have_is_left_to_it(self):
        # The common case, and the cheap one: the address goes to the browser and this
        # server never carries the image at all.
        login = FakeSocialLogin(User(username="kim"), {"picture": PICTURE}, token="tok")

        with patch("sso.avatars.requests.get", return_value=fake_answer()) as get:
            remember_picture(self.request, sociallogin=login)

        self.assertEqual(self.request.session[SESSION_KEY], PICTURE)
        self.assertIsNone(self.request.session[SESSION_PICTURE_KEY])
        # Only the probe. Nothing was downloaded, and no token was sent anywhere.
        self.assertEqual(get.call_count, 1)
        self.assertNotIn("headers", get.call_args.kwargs)

    def test_a_picture_only_the_provider_will_hand_over_is_downloaded(self):
        # Entra ID's shape: the browser is refused, so the login fetches the photograph
        # with the token it has just been given and the sidebar points back at this system.
        login = FakeSocialLogin(
            User(username="kim"), {"picture": GRAPH_PICTURE}, token="token-123"
        )
        refused = fake_answer(status=401, content_type="application/json")

        with (
            patch("sso.avatars.api_host", return_value="graph.microsoft.com"),
            patch("sso.avatars.requests.get", side_effect=[refused, fake_answer()]),
        ):
            remember_picture(self.request, sociallogin=login)

        self.assertEqual(self.request.session[SESSION_KEY], reverse("avatar"))
        self.assertEqual(
            b64decode(self.request.session[SESSION_PICTURE_KEY]["data"]), PHOTO
        )

    def test_a_picture_that_cannot_be_had_at_all_leaves_the_initial(self):
        login = FakeSocialLogin(
            User(username="kim"), {"picture": GRAPH_PICTURE}, token="token-123"
        )
        refused = fake_answer(status=401, content_type="application/json")

        with (
            patch("sso.avatars.api_host", return_value="graph.microsoft.com"),
            patch("sso.avatars.requests.get", return_value=refused),
        ):
            remember_picture(self.request, sociallogin=login)

        self.assertIsNone(self.request.session[SESSION_KEY])
        self.assertIsNone(self.request.session[SESSION_PICTURE_KEY])

    def test_nothing_is_downloaded_without_a_token(self):
        # A login that never carried one — there is nothing to ask the provider with.
        login = FakeSocialLogin(User(username="kim"), {"picture": GRAPH_PICTURE})
        refused = fake_answer(status=401, content_type="application/json")

        with (
            patch("sso.avatars.api_host") as host,
            patch("sso.avatars.requests.get", return_value=refused),
        ):
            remember_picture(self.request, sociallogin=login)

        self.assertIsNone(self.request.session[SESSION_KEY])
        host.assert_not_called()


class ScopeChoiceTests(TestCase):
    """Working out what to ask a provider for, so that nobody has to fill it in."""

    def test_a_provider_is_asked_for_the_standard_scopes(self):
        self.assertEqual(
            scopes_for("https://keycloak.example/realms/company"),
            ["openid", "profile", "email"],
        )

    def test_entra_is_also_asked_for_what_its_pictures_need(self):
        # Not discoverable: Entra ID's own document lists only the OpenID Connect scopes,
        # and User.Read is a Microsoft Graph permission that never appears in it.
        self.assertEqual(
            scopes_for("https://login.microsoftonline.com/a-tenant-id/v2.0"),
            ["openid", "profile", "email", "User.Read"],
        )

    def test_the_sovereign_clouds_count_as_entra_too(self):
        for issuer in (
            "https://login.microsoftonline.us/tenant/v2.0",
            "https://login.partner.microsoftonline.cn/tenant/v2.0",
        ):
            self.assertIn("User.Read", scopes_for(issuer), issuer)

    def test_a_lookalike_domain_is_not_entra(self):
        # Matching on the letters at the end alone would take "notmicrosoftonline.com" for
        # Microsoft, and ask a stranger's provider for a permission it has never heard of.
        self.assertEqual(
            scopes_for("https://login.notmicrosoftonline.com/tenant/v2.0"),
            ["openid", "profile", "email"],
        )

    def test_an_operator_can_overrule_the_choice(self):
        # The way back for a tenant that will not grant the extra permission, whose
        # sign-in then fails outright rather than merely losing the picture.
        self.assertEqual(
            scopes_for(
                "https://login.microsoftonline.com/a-tenant-id/v2.0",
                "openid profile email",
            ),
            ["openid", "profile", "email"],
        )

    def test_an_unconfigured_issuer_asks_for_the_standard_scopes(self):
        # What every installation with single sign-on switched off carries.
        self.assertEqual(scopes_for(""), ["openid", "profile", "email"])


@skipUnless(settings.OIDC_ENABLED, "the provider is only configured when it is on")
class ScopeTests(TestCase):
    """What the sign-in asks the provider for.

    A setting because of the picture: Entra ID hands that over only to a token holding
    "User.Read", while a provider that has never heard of the scope refuses the request
    outright. What matters here is that allauth reads the setting at all — put under the
    wrong key it would be ignored in silence, and the operator would be left adding a
    scope that never reached the provider.
    """

    def provider(self):
        from allauth.socialaccount.adapter import get_adapter

        request = RequestFactory().get("/")
        return get_adapter().get_provider(request, provider=settings.OIDC_PROVIDER_ID)

    def test_the_standard_scopes_are_asked_for_by_default(self):
        self.assertEqual(self.provider().get_scope(), ["openid", "profile", "email"])

    def test_a_configured_scope_reaches_the_provider(self):
        app = settings.SOCIALACCOUNT_PROVIDERS["openid_connect"]["APPS"][0]
        extra = dict(app, settings=dict(app["settings"], scope=["openid", "User.Read"]))
        providers = {"openid_connect": {"APPS": [extra]}}

        with override_settings(SOCIALACCOUNT_PROVIDERS=providers):
            self.assertEqual(self.provider().get_scope(), ["openid", "User.Read"])


class AvatarViewTests(TestCase):
    """Serving a downloaded picture back to the browser that will draw it."""

    def store(self):
        session = self.client.session
        session[SESSION_PICTURE_KEY] = {
            "content_type": "image/jpeg",
            "data": b64encode(PHOTO).decode(),
        }
        session.save()

    def test_the_picture_is_served(self):
        self.store()

        response = self.client.get(reverse("avatar"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, PHOTO)
        self.assertEqual(response["Content-Type"], "image/jpeg")

    def test_it_is_kept_by_the_browser_and_by_no_one_else(self):
        # One person's face must not sit in a shared cache waiting to be served to the
        # next, and the browser should not ask again on every page view.
        self.store()

        response = self.client.get(reverse("avatar"))

        self.assertIn("private", response["Cache-Control"])
        self.assertIn("max-age", response["Cache-Control"])

    def test_a_session_without_one_has_none_to_give(self):
        self.assertEqual(self.client.get(reverse("avatar")).status_code, 404)


@skipUnless(settings.OIDC_ENABLED, "the signal is allauth's, and is connected with it")
class LoginSignalTests(TestCase):
    """The wiring, which is the half that cannot fail loudly.

    remember_picture works when called; what is worth proving is that a login calls it.
    The signal is sent exactly as allauth sends it, so the arguments it arrives with are
    part of what is under test.
    """

    def test_a_provider_login_stores_the_picture(self):
        from allauth.account.signals import user_logged_in

        request = RequestFactory().get("/")
        request.session = {}
        login = FakeSocialLogin(User(username="kim"), {"picture": PICTURE})

        with patch("sso.avatars.requests.get", return_value=fake_answer()):
            user_logged_in.send(
                sender=User,
                request=request,
                response=None,
                user=login.user,
                sociallogin=login,
            )

        self.assertEqual(request.session[SESSION_KEY], PICTURE)


class AvatarMiddlewareTests(TestCase):
    """Putting the picture where Unfold reads it."""

    def test_the_picture_is_put_on_the_user(self):
        request = RequestFactory().get("/")
        request.session = {SESSION_KEY: PICTURE}
        request.user = User(username="kim")

        middleware(lambda _: None)(request)

        self.assertEqual(request.user.avatar_url, PICTURE)

    def test_a_session_without_one_leaves_the_user_alone(self):
        # Assigning to request.user resolves the lazy object Django leaves there, which is
        # a query for the user — on every request, and most of them render no sidebar.
        loaded = []

        def load_user():
            loaded.append(1)
            return User(username="kim")

        request = RequestFactory().get("/")
        request.session = {}
        request.user = SimpleLazyObject(load_user)

        middleware(lambda _: None)(request)

        self.assertEqual(loaded, [])


@skipUnless(settings.OIDC_ENABLED, "the middleware is installed with single sign-on")
class AvatarInTheSidebarTests(TestCase):
    """What all of it is for: Unfold showing the picture where the initial was."""

    def setUp(self):
        self.client.force_login(User.objects.create_superuser("kim"))

    def test_the_picture_is_shown(self):
        session = self.client.session
        session[SESSION_KEY] = PICTURE
        session.save()

        self.assertContains(self.client.get(reverse("admin:index")), PICTURE)

    def test_a_session_without_one_shows_the_initial(self):
        # Unfold's own fallback, which is the whole of the feature for a provider that
        # publishes no picture — and must stay reachable rather than being replaced by a
        # blank circle.
        self.assertNotContains(self.client.get(reverse("admin:index")), "background-image")


class DatabaseAccessSwitchTests(TestCase):
    """The switch on the user page, which is how a database account is given and taken.

    Everything below the switch — the role name, the rank, the credential handover — is
    covered in dbusers/tests.py. What matters here is that the switch reflects what
    exists and that saving reconciles it.
    """

    def setUp(self):
        for name in (VIEWER, EDITOR, ADMIN):
            Group.objects.get_or_create(name=name)
        self.client.force_login(User.objects.create_superuser("root"))
        self.user = User.objects.create_user("jdoe", password="x")
        self.user.groups.add(Group.objects.get(name=EDITOR))

    def _post(self, **overrides):
        """Save the user's change page with the switch in a given position.

        Args:
            **overrides: Form fields to set, notably database_access.

        Returns:
            The response.
        """
        data = {
            "username": self.user.username,
            "first_name": "",
            "last_name": "",
            "email": "",
            "is_active": "on",
            "groups": [str(group.pk) for group in self.user.groups.all()],
            "user_permissions": [],
            "last_login_0": "",
            "last_login_1": "",
            "date_joined_0": "2026-01-01",
            "date_joined_1": "00:00:00",
        }
        data.update(overrides)
        if settings.OIDC_ENABLED:
            data.update({
                "socialaccount_set-TOTAL_FORMS": "0",
                "socialaccount_set-INITIAL_FORMS": "0",
            })
        return self.client.post(
            reverse("admin:auth_user_change", args=[self.user.pk]), data
        )

    def test_switching_it_on_enrolls(self):
        with patch("dbusers.utils.connection"):
            self._post(database_access="on")

        self.assertTrue(DatabaseUser.objects.filter(user=self.user).exists())

    def test_switching_it_off_removes(self):
        with patch("dbusers.utils.connection"):
            enroll(self.user)
            self._post()

        self.assertFalse(DatabaseUser.objects.filter(user=self.user).exists())

    def test_saving_without_touching_it_leaves_the_account_alone(self):
        with patch("dbusers.utils.connection"):
            enroll(self.user)

        with patch("dbusers.utils.connection") as conn:
            self._post(database_access="on")

        # No re-enrollment: create_db_user would rewrite the role the person already has.
        conn.cursor.assert_not_called()
        self.assertTrue(DatabaseUser.objects.filter(user=self.user).exists())

    def test_the_switch_shows_the_account_that_exists(self):
        with patch("dbusers.utils.connection"):
            enroll(self.user)

        page = self.client.get(reverse("admin:auth_user_change", args=[self.user.pk]))
        self.assertTrue(page.context["adminform"].form["database_access"].value())

    def test_the_switch_is_rendered_as_a_switch(self):
        """Unfold's formfield_overrides reach model fields only, so a plain form field
        silently falls back to Django's bare checkbox."""
        page = self.client.get(reverse("admin:auth_user_change", args=[self.user.pk]))
        widget = page.context["adminform"].form.fields["database_access"].widget
        self.assertIsInstance(widget, UnfoldBooleanSwitchWidget)

    def test_a_superuser_needs_no_rank_group(self):
        """With single sign-on off nothing grants a role group, so the local
        administrator would otherwise be the one person unable to get an account."""
        self.user.groups.clear()
        self.user.is_superuser = True
        self.user.save()

        with patch("dbusers.utils.connection"):
            self._post(database_access="on", groups=[], is_superuser="on")

        self.assertTrue(DatabaseUser.objects.filter(user=self.user).exists())

    def test_a_user_without_a_rank_cannot_be_switched_on(self):
        """Without a rank there is no privilege set to grant, so the switch is disabled
        rather than failing on save."""
        self.user.groups.clear()

        with patch("dbusers.utils.connection") as conn:
            self._post(database_access="on", groups=[])

        conn.cursor.assert_not_called()
        self.assertFalse(DatabaseUser.objects.filter(user=self.user).exists())

    def test_a_database_failure_does_not_lose_the_user_edit(self):
        # The admin logs the failure it recovers from, so muffle the traceback the
        # deliberate error would otherwise print across the test output.
        self.addCleanup(disable, 0)
        disable(CRITICAL)
        with patch("dbusers.utils.enroll", side_effect=RuntimeError("boom")), \
                patch("sso.admin.enroll", side_effect=RuntimeError("boom")):
            self._post(database_access="on", first_name="John")

        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, "John")
        self.assertFalse(DatabaseUser.objects.filter(user=self.user).exists())


class DatabaseAccessColumnTests(TestCase):
    """The user list, where an operator asks who may reach the database."""

    def setUp(self):
        Group.objects.get_or_create(name=EDITOR)
        self.client.force_login(User.objects.create_superuser("root"))
        self.with_access = User.objects.create_user("jdoe")
        self.with_access.groups.add(Group.objects.get(name=EDITOR))
        with patch("dbusers.utils.connection"):
            enroll(self.with_access)
        self.without = User.objects.create_user("someone_else")

    def test_the_column_is_shown(self):
        page = self.client.get(reverse("admin:auth_user_changelist"))
        self.assertIn("has_database_access", page.context["cl"].list_display)

    def test_filtering_to_those_who_have_it(self):
        page = self.client.get(
            reverse("admin:auth_user_changelist"), {"database_access": "1"}
        )
        listed = {user.username for user in page.context["cl"].queryset}
        self.assertEqual(listed, {"jdoe"})

    def test_filtering_to_those_who_do_not(self):
        page = self.client.get(
            reverse("admin:auth_user_changelist"), {"database_access": "0"}
        )
        listed = {user.username for user in page.context["cl"].queryset}
        self.assertEqual(listed, {"root", "someone_else"})
