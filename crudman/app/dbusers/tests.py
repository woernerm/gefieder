"""Unit tests for the provisioning logic, with the database mocked.

What is tested here is what the database is asked for: the rank a person's groups earn,
the role name derived from their username, and the reconciliation on every login. The
functions themselves are covered against the live stack in tests/test_db_users.py, the
admin switch in sso/tests.py.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.contrib.messages.storage.base import BaseStorage
from django.test import RequestFactory, TestCase
from django.urls import reverse
from sso.roles import GROUP_FOR_RANK

from .backends import ScramBackend, get_backend
from .models import DatabaseUser
from .signals import sync_on_login
from .utils import (
    USER_PREFIX,
    db_role_for_user,
    disable,
    enroll,
    issue_password,
    issue_token,
    remove,
    reset,
    role_name_for,
    sync,
    unmanaged_role,
    user_for_token,
)

# Derived from the configured prefixes, not the literals they happen to produce at their
# defaults, which would only assert that buildtime.env is unchanged.
VIEWER = VIEWER_GROUP = GROUP_FOR_RANK["viewer"]
EDITOR = EDITOR_GROUP = GROUP_FOR_RANK["editor"]
ADMIN = ADMIN_GROUP = GROUP_FOR_RANK["admin"]
JDOE = f"{USER_PREFIX}jdoe"


class CollectingStorage(BaseStorage):
    """Message storage that only remembers, for a request built without a session."""

    def _get(self, *args, **kwargs):
        return [], True

    def _store(self, messages, response, *args, **kwargs):
        return []


class UnmanagedRoleTests(TestCase):
    """Whether the name a user would be provisioned under is already somebody else's.

    The switch reports access it cannot manage rather than offering to create a role that
    create_db_user would refuse.
    """

    def setUp(self):
        self.user = User.objects.create_user("jdoe")

    def test_a_free_name_is_not_reported(self):
        with patch("dbusers.utils.connection") as conn:
            conn.cursor.return_value.__enter__.return_value.fetchone.return_value = None
            self.assertIsNone(unmanaged_role(self.user))

    def test_a_name_taken_by_another_role_is_reported(self):
        with patch("dbusers.utils.connection") as conn:
            conn.cursor.return_value.__enter__.return_value.fetchone.return_value = (1,)
            self.assertEqual(unmanaged_role(self.user), role_name_for("jdoe"))

    def test_an_account_this_app_provisioned_is_not_reported(self):
        """Its own row answers the question, so the database is not asked at all."""
        DatabaseUser.objects.create(user=self.user, role_name=role_name_for("jdoe"),
                                    group_role=GROUP_FOR_RANK["viewer"])

        with patch("dbusers.utils.connection") as conn:
            self.assertIsNone(unmanaged_role(self.user))

        conn.cursor.assert_not_called()


class RoleNameTests(TestCase):
    def test_username_is_slugged(self):
        self.assertEqual(role_name_for("jdoe"), JDOE)

    def test_email_username_becomes_an_identifier(self):
        """Providers commonly send an email address, which PostgreSQL will not accept."""
        self.assertEqual(
            role_name_for("John.Doe@example.com"),
            f"{USER_PREFIX}john_doe_example_com",
        )

    def test_name_is_capped_to_the_identifier_limit(self):
        """The database function rejects anything over 50 characters."""
        self.assertLessEqual(len(role_name_for("x" * 200)), 50)

    def test_prefix_keeps_the_name_from_starting_with_a_digit(self):
        self.assertTrue(role_name_for("1st.analyst").startswith(USER_PREFIX))


class RankTests(TestCase):
    def setUp(self):
        for name in (VIEWER_GROUP, EDITOR_GROUP, ADMIN_GROUP):
            Group.objects.get_or_create(name=name)
        self.user = User.objects.create(username="jdoe")

    def test_no_group_means_no_database_access(self):
        self.assertIsNone(db_role_for_user(self.user))

    def test_a_superuser_ranks_as_admin_without_a_group(self):
        """Single sign-on is what grants the rank groups, and it may be switched off."""
        self.user.is_superuser = True
        self.assertEqual(db_role_for_user(self.user), ADMIN)

    def test_a_group_still_wins_for_a_superuser(self):
        """The exemption is a floor, not an override."""
        self.user.is_superuser = True
        self.user.groups.add(Group.objects.get(name=ADMIN_GROUP))
        self.assertEqual(db_role_for_user(self.user), ADMIN)

    def test_group_maps_to_rank(self):
        self.user.groups.add(Group.objects.get(name=EDITOR_GROUP))
        self.assertEqual(db_role_for_user(self.user), EDITOR)

    def test_highest_rank_wins(self):
        """The most privileged group decides, as sso.roles.highest_role also does."""
        self.user.groups.add(Group.objects.get(name=VIEWER_GROUP))
        self.user.groups.add(Group.objects.get(name=ADMIN_GROUP))
        self.assertEqual(db_role_for_user(self.user), ADMIN)


class SyncTests(TestCase):
    def setUp(self):
        for name in (VIEWER_GROUP, EDITOR_GROUP, ADMIN_GROUP):
            Group.objects.get_or_create(name=name)
        self.user = User.objects.create(username="jdoe")
        self.user.groups.add(Group.objects.get(name=VIEWER_GROUP))
        self.record = DatabaseUser.objects.create(
            user=self.user,
            role_name=JDOE,
            group_role=VIEWER,
        )

    def test_person_without_an_account_is_left_alone(self):
        """Logging in must not provision anyone: that is an administrator's act."""
        other = User.objects.create(username="someone_else")
        with patch("dbusers.utils.connection") as conn:
            sync(other)
        conn.cursor.assert_not_called()

    def test_unchanged_rank_touches_nothing(self):
        with patch("dbusers.utils.connection") as conn:
            sync(self.user)
        conn.cursor.assert_not_called()

    def test_promotion_is_applied(self):
        self.user.groups.clear()
        self.user.groups.add(Group.objects.get(name=ADMIN_GROUP))
        with patch("dbusers.utils.connection") as conn:
            sync(self.user)

        cursor = conn.cursor.return_value.__enter__.return_value
        sql, params = cursor.execute.call_args[0]
        self.assertIn("create_db_user", sql)
        # A NULL password: re-ranking must not issue a new credential.
        self.assertEqual(params, [JDOE, None, ADMIN])
        self.record.refresh_from_db()
        self.assertEqual(self.record.group_role, ADMIN)

    def test_losing_every_role_disables_the_account(self):
        self.user.groups.clear()
        with patch("dbusers.utils.connection") as conn:
            sync(self.user)

        cursor = conn.cursor.return_value.__enter__.return_value
        sql, params = cursor.execute.call_args[0]
        self.assertIn("delete_db_user", sql)
        self.assertEqual(params, [JDOE])
        self.record.refresh_from_db()
        self.assertFalse(self.record.is_enabled)

    def test_deactivated_account_is_disabled(self):
        """is_active is what sso.roles.apply_roles clears when the provider drops someone."""
        self.user.is_active = False
        self.user.save()
        with patch("dbusers.utils.connection") as conn:
            sync(self.user)

        cursor = conn.cursor.return_value.__enter__.return_value
        self.assertIn("delete_db_user", cursor.execute.call_args[0][0])


class BackendTests(TestCase):
    def test_scram_issues_a_secret_long_enough_for_the_database_check(self):
        """create_db_user refuses anything under 12 characters."""
        secret = ScramBackend().make_secret()
        self.assertGreaterEqual(len(secret), 12)

    def test_secrets_are_not_reused(self):
        backend = ScramBackend()
        self.assertNotEqual(backend.make_secret(), backend.make_secret())

    def test_active_backend_makes_a_password(self):
        """Guards the swap point: a backend whose provider held the credential would
        return None here, and issue_password would set an empty one."""
        self.assertTrue(get_backend().make_secret())


class CredentialHandoverTests(TestCase):
    """Who gets to see the password, and when.

    Splitting enrollment from issuing is what keeps an administrator from learning
    someone else's credential, and keeps any secret from being stored while it waits.
    """

    def setUp(self):
        Group.objects.get_or_create(name=EDITOR_GROUP)
        self.user = User.objects.create(username="jdoe")
        self.user.groups.add(Group.objects.get(name=EDITOR_GROUP))

    def test_enrolling_creates_the_role_without_a_password(self):
        """The administrator decides that someone gets access; the password is issued
        later, to the person's own client, so no administrator ever learns one."""
        with patch("dbusers.utils.connection") as conn:
            enroll(self.user)

        cursor = conn.cursor.return_value.__enter__.return_value
        sql, params = cursor.execute.call_args[0]
        self.assertIn("create_db_user", sql)
        self.assertEqual(params, [JDOE, None, EDITOR])

    def test_signing_in_hands_over_no_password(self):
        """Nothing is shown to copy down: a password is issued when it is about to be
        used and expires by itself, so a message here would only leak one."""
        with patch("dbusers.utils.connection"):
            enroll(self.user)

        request = RequestFactory().get("/")
        # A bare request has no session, which the configured storage needs.
        request._messages = CollectingStorage(request)

        with patch("dbusers.utils.connection"):
            sync_on_login(None, request, self.user)

        self.assertEqual([str(m) for m in request._messages], [])

    def test_issuing_sets_an_expiring_password_on_the_persons_role(self):
        with patch("dbusers.utils.connection"):
            enroll(self.user)

        with patch("dbusers.utils.connection") as conn:
            cursor = conn.cursor.return_value.__enter__.return_value
            cursor.fetchone.return_value = (datetime(2030, 1, 1, tzinfo=timezone.utc),)
            role, secret, expires_at = issue_password(self.user, timedelta(hours=12))

        sql, params = cursor.execute.call_args[0]
        self.assertIn("issue_db_user_password", sql)
        self.assertEqual(params[0], JDOE)
        self.assertEqual(params[1], secret)
        self.assertEqual(params[2], timedelta(hours=12))
        self.assertEqual(role, JDOE)
        self.assertEqual(expires_at.year, 2030)

    def test_issuing_again_gives_a_different_password(self):
        """Rotated per use, which is what keeps a leaked one bounded."""
        with patch("dbusers.utils.connection"):
            enroll(self.user)

        with patch("dbusers.utils.connection") as conn:
            conn.cursor.return_value.__enter__.return_value.fetchone.return_value = (None,)
            first = issue_password(self.user, timedelta(hours=12))[1]
            second = issue_password(self.user, timedelta(hours=12))[1]

        self.assertNotEqual(first, second)

    def test_nothing_is_stored_anywhere(self):
        """The secret exists only in the return value; no field holds it."""
        with patch("dbusers.utils.connection") as conn:
            enroll(self.user)
            conn.cursor.return_value.__enter__.return_value.fetchone.return_value = (None,)
            secret = issue_password(self.user, timedelta(hours=12))[1]

        record = DatabaseUser.objects.get(user=self.user)
        stored = " ".join(str(value) for value in record.__dict__.values())
        self.assertNotIn(secret, stored)

    def test_someone_without_an_account_is_issued_nothing(self):
        other = User.objects.create(username="someone_else")
        with patch("dbusers.utils.connection"):
            with self.assertRaises(ValueError):
                issue_password(other, timedelta(hours=12))

    def test_reset_clears_the_password_and_the_token(self):
        """Clearing the password alone would leave a token able to mint another."""
        with patch("dbusers.utils.connection"):
            enroll(self.user)
            issue_token(self.user)

        with patch("dbusers.utils.connection") as conn:
            reset(self.user)

        cursor = conn.cursor.return_value.__enter__.return_value
        sql, params = cursor.execute.call_args[0]
        self.assertIn("clear_db_user_password", sql)
        self.assertEqual(params, [JDOE])
        self.assertEqual(DatabaseUser.objects.get(user=self.user).token, "")


class NonStaffTests(TestCase):
    """A database account does not depend on reaching the admin.

    Someone may query the warehouse without administering anything, so the rank groups
    decide and is_staff is not consulted.
    """

    def setUp(self):
        Group.objects.get_or_create(name=VIEWER_GROUP)
        self.user = User.objects.create(username="jdoe", is_staff=False)
        self.user.groups.add(Group.objects.get(name=VIEWER_GROUP))

    def test_a_non_staff_user_can_be_enrolled(self):
        with patch("dbusers.utils.connection"):
            record = enroll(self.user)
        self.assertEqual(record.group_role, VIEWER)

    def test_losing_staff_status_does_not_disable_the_account(self):
        with patch("dbusers.utils.connection"):
            enroll(self.user)

        with patch("dbusers.utils.connection") as conn:
            sync(self.user)

        conn.cursor.assert_not_called()
        self.assertTrue(DatabaseUser.objects.get(user=self.user).is_enabled)


class RemovalTests(TestCase):
    def setUp(self):
        Group.objects.get_or_create(name=EDITOR_GROUP)
        self.user = User.objects.create(username="jdoe")
        self.user.groups.add(Group.objects.get(name=EDITOR_GROUP))
        with patch("dbusers.utils.connection"):
            enroll(self.user)

    def test_remove_drops_the_role_and_the_row(self):
        with patch("dbusers.utils.connection") as conn:
            remove(self.user)

        cursor = conn.cursor.return_value.__enter__.return_value
        sql, params = cursor.execute.call_args[0]
        self.assertIn("drop_db_user", sql)
        self.assertEqual(params, [JDOE])
        self.assertFalse(DatabaseUser.objects.filter(user=self.user).exists())

    def test_removing_an_account_that_does_not_exist_is_refused(self):
        other = User.objects.create(username="someone_else")
        with patch("dbusers.utils.connection"):
            with self.assertRaises(ValueError):
                remove(other)

    def test_the_person_can_be_enrolled_again_afterwards(self):
        """Deleting is not a tombstone: the same person may be given a new account."""
        with patch("dbusers.utils.connection"):
            remove(self.user)
            record = enroll(self.user)

        self.assertEqual(record.role_name, JDOE)
        self.assertEqual(record.token, "", "a new account starts with no token")


class TokenTests(TestCase):
    """The token a developer's checkout authenticates with.

    One per person, and worth exactly the database access it stands for: it mints a
    short-lived password for its owner and does nothing else.
    """

    def setUp(self):
        self.user = User.objects.create_user("jdoe", password="x", is_staff=True)
        self.user.groups.add(Group.objects.get_or_create(name=GROUP_FOR_RANK["editor"])[0])
        self.record = DatabaseUser.objects.create(
            user=self.user,
            role_name="jdoe",
            group_role=GROUP_FOR_RANK["editor"],
            is_enabled=True,
        )

    def test_issuing_replaces_the_previous_token(self):
        """One per person: creating a new one is how a leaked token is taken back."""
        first = issue_token(self.user)
        second = issue_token(self.user)

        self.assertNotEqual(first, second)
        self.assertIsNone(user_for_token(first))
        self.assertEqual(user_for_token(second), self.user)

    def test_an_unknown_token_names_nobody(self):
        self.assertIsNone(user_for_token("nonsense"))

    def test_an_empty_token_names_nobody(self):
        """A missing Authorization header must not match a person whose token is blank."""
        self.assertIsNone(user_for_token(""))

    def test_switching_database_access_off_revokes_the_token(self):
        """The token mints passwords, so it must not outlive the access it stands for."""
        token = issue_token(self.user)

        with patch("dbusers.utils.connection"):
            disable(self.record.role_name)

        self.assertIsNone(user_for_token(token))

    def test_a_deactivated_person_holds_no_usable_token(self):
        """Losing every role in the provider deactivates the account; it must close this
        door too, not only the admin's."""
        token = issue_token(self.user)
        self.user.is_active = False
        self.user.save()

        self.assertIsNone(user_for_token(token))


class PasswordEndpointTests(TestCase):
    """What a checkout gets when it presents its token."""

    def setUp(self):
        self.url = reverse("dbusers:password")
        self.user = User.objects.create_user("jdoe", password="x", is_staff=True)
        self.user.groups.add(Group.objects.get_or_create(name=GROUP_FOR_RANK["editor"])[0])
        DatabaseUser.objects.create(
            user=self.user,
            role_name="jdoe",
            group_role=GROUP_FOR_RANK["editor"],
            is_enabled=True,
        )

    def _post(self, token):
        return self.client.post(self.url, headers={"authorization": f"Bearer {token}"})

    def test_a_valid_token_gets_a_password(self):
        token = issue_token(self.user)

        with patch("dbusers.utils.connection") as connection:
            connection.cursor.return_value.__enter__.return_value.fetchone.return_value = (
                datetime(2030, 1, 1, tzinfo=timezone.utc),
            )
            answer = self._post(token).json()

        self.assertEqual(answer["db_user"], "jdoe")
        self.assertTrue(answer["db_password"])
        self.assertIn("2030", answer["expires_at"])

    def test_an_unknown_token_is_refused(self):
        self.assertEqual(self._post("nonsense").status_code, 401)

    def test_a_request_without_a_token_is_refused(self):
        self.assertEqual(self.client.post(self.url).status_code, 401)

    def test_a_request_through_the_proxy_is_answered(self):
        """Unlike notebooks/whoami, which only the hub inside the pod may call: this one
        exists for a laptop, so it has to survive the proxy's forwarding header."""
        token = issue_token(self.user)

        with patch("dbusers.utils.connection") as connection:
            connection.cursor.return_value.__enter__.return_value.fetchone.return_value = (
                datetime(2030, 1, 1, tzinfo=timezone.utc),
            )
            response = self.client.post(
                self.url,
                headers={
                    "authorization": f"Bearer {token}",
                    "x-forwarded-for": "203.0.113.7",
                },
            )

        self.assertEqual(response.status_code, 200)

    def test_the_password_is_never_cached(self):
        token = issue_token(self.user)

        with patch("dbusers.utils.connection") as connection:
            connection.cursor.return_value.__enter__.return_value.fetchone.return_value = (
                datetime(2030, 1, 1, tzinfo=timezone.utc),
            )
            response = self._post(token)

        self.assertEqual(response.headers["Cache-Control"], "no-store")


class AccessTokenPageTests(TestCase):
    """The page behind the user menu where a person creates their own token."""

    def setUp(self):
        self.url = reverse("admin:auth_accesstoken_changelist")
        self.create_url = reverse("admin:dbusers_accesstoken_create")
        self.user = User.objects.create_user("jdoe", password="x", is_staff=True)
        self.user.groups.add(Group.objects.get_or_create(name=GROUP_FOR_RANK["editor"])[0])
        DatabaseUser.objects.create(
            user=self.user,
            role_name="jdoe",
            group_role=GROUP_FOR_RANK["editor"],
            is_enabled=True,
        )
        self.client.force_login(self.user)

    def test_the_page_is_reached_from_the_user_menu_and_not_the_app_list(self):
        """Every page draws the menu, so the index page does; the app list does not name
        it, since a link there would take a section's worth of room for one page."""
        response = self.client.get(reverse("admin:index"))
        page = response.content.decode()

        self.assertIn(self.url, page)
        listed = [
            model["name"]
            for app in response.context["available_apps"]
            for model in app["models"]
        ]
        self.assertNotIn("Access token", listed)

    def test_the_page_offers_a_button_before_any_token_exists(self):
        page = self.client.get(self.url).content.decode()

        self.assertIn("no token yet", page)
        self.assertIn(self.create_url, page)

    def test_posting_creates_a_token_and_shows_it_once(self):
        response = self.client.post(self.create_url)
        page = response.content.decode()

        token = DatabaseUser.objects.get(user=self.user).token
        self.assertTrue(token)
        self.assertIn(token, page)

        # Shown once: coming back to the page must not repeat it.
        self.assertNotIn(token, self.client.get(self.url).content.decode())

    def test_a_get_creates_nothing(self):
        """A page view must not invalidate the token a checkout is using."""
        self.client.get(self.create_url)

        self.assertFalse(DatabaseUser.objects.get(user=self.user).token)

    def test_it_acts_on_the_signed_in_person_alone(self):
        """An administrator grants the access; the credential is the account holder's."""
        other = User.objects.create_user("mmustermann", is_staff=True)
        DatabaseUser.objects.create(
            user=other,
            role_name="mmustermann",
            group_role=GROUP_FOR_RANK["editor"],
            is_enabled=True,
        )

        self.client.post(self.create_url)

        self.assertTrue(DatabaseUser.objects.get(user=self.user).token)
        self.assertFalse(DatabaseUser.objects.get(user=other).token)

    def test_someone_without_an_account_is_told_what_to_ask_for(self):
        DatabaseUser.objects.filter(user=self.user).delete()

        page = self.client.get(self.url).content.decode()

        self.assertIn("Database access", page)
