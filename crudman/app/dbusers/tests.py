"""Unit tests for the provisioning logic, with the database mocked.

The database functions themselves are covered against the live stack in
tests/test_db_users.py. What is worth testing here is the part that decides *what* to ask
the database for: the rank a person's groups earn them, the role name derived from their
username, and the reconciliation that runs on every login.

The switch that drives all this from the user page is tested in sso/tests.py, where the
admin it lives on is.
"""
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.test import TestCase
from sso.roles import GROUP_FOR_RANK

from .backends import ScramBackend, get_backend
from .models import DatabaseUser
from .utils import (
    GROUP_TO_DB_ROLE,
    ROLE_PREFIX,
    db_role_for_user,
    enroll,
    issue_credential,
    remove,
    reset,
    role_name_for,
    sync,
)

# The names the configured DB_ROLE_PREFIX produces, rather than the ones it happens to
# produce at its default: what these tests are about is the derivation, so pinning the
# literals here would only assert that buildtime.env is unchanged.
VIEWER = GROUP_TO_DB_ROLE[GROUP_FOR_RANK["viewer"]]
EDITOR = GROUP_TO_DB_ROLE[GROUP_FOR_RANK["editor"]]
ADMIN = GROUP_TO_DB_ROLE[GROUP_FOR_RANK["admin"]]
MARCUS = f"{ROLE_PREFIX}marcus"

# The Django groups those ranks come from, named the way sso.roles builds them.
VIEWER_GROUP = GROUP_FOR_RANK["viewer"]
EDITOR_GROUP = GROUP_FOR_RANK["editor"]
ADMIN_GROUP = GROUP_FOR_RANK["admin"]


class RoleNameTests(TestCase):
    def test_username_is_slugged(self):
        self.assertEqual(role_name_for("marcus"), MARCUS)

    def test_email_username_becomes_an_identifier(self):
        """An email-address username becomes a valid identifier.

        Providers commonly send an email address as the username, which
        PostgreSQL will not accept as one."""
        self.assertEqual(
            role_name_for("Marcus.Woerner@example.com"),
            f"{ROLE_PREFIX}marcus_woerner_example_com",
        )

    def test_name_is_capped_to_the_identifier_limit(self):
        """The database function rejects anything over 50 characters."""
        self.assertLessEqual(len(role_name_for("x" * 200)), 50)

    def test_prefix_keeps_the_name_from_starting_with_a_digit(self):
        self.assertTrue(role_name_for("1st.analyst").startswith(ROLE_PREFIX))


class RankTests(TestCase):
    def setUp(self):
        for name in (VIEWER_GROUP, EDITOR_GROUP, ADMIN_GROUP):
            Group.objects.get_or_create(name=name)
        self.user = User.objects.create(username="marcus")

    def test_no_group_means_no_database_access(self):
        self.assertIsNone(db_role_for_user(self.user))

    def test_a_superuser_ranks_as_admin_without_a_group(self):
        """Single sign-on is what grants the rank groups, and it may be switched off."""
        self.user.is_superuser = True
        self.assertEqual(db_role_for_user(self.user), ADMIN)

    def test_a_group_still_wins_for_a_superuser(self):
        """The exemption is a floor, not an override: it only fills the gap where no
        group speaks at all."""
        self.user.is_superuser = True
        self.user.groups.add(Group.objects.get(name=ADMIN_GROUP))
        self.assertEqual(db_role_for_user(self.user), ADMIN)

    def test_group_maps_to_rank(self):
        self.user.groups.add(Group.objects.get(name=EDITOR_GROUP))
        self.assertEqual(db_role_for_user(self.user), EDITOR)

    def test_highest_rank_wins(self):
        """The most privileged group decides the rank.

        Someone may hold several groups at once, matching how
        sso.roles.highest_role resolves the same ambiguity."""
        self.user.groups.add(Group.objects.get(name=VIEWER_GROUP))
        self.user.groups.add(Group.objects.get(name=ADMIN_GROUP))
        self.assertEqual(db_role_for_user(self.user), ADMIN)


class SyncTests(TestCase):
    def setUp(self):
        for name in (VIEWER_GROUP, EDITOR_GROUP, ADMIN_GROUP):
            Group.objects.get_or_create(name=name)
        self.user = User.objects.create(username="marcus")
        self.user.groups.add(Group.objects.get(name=VIEWER_GROUP))
        self.record = DatabaseUser.objects.create(
            user=self.user,
            role_name=MARCUS,
            group_role=VIEWER,
            awaiting_credential=False,
        )

    def test_person_without_an_account_is_left_alone(self):
        """Logging in must not provision anyone.

        Provisioning issues a credential, which has to be a deliberate act by an
        administrator."""
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
        self.assertEqual(params, [MARCUS, None, ADMIN])
        self.record.refresh_from_db()
        self.assertEqual(self.record.group_role, ADMIN)

    def test_losing_every_role_disables_the_account(self):
        self.user.groups.clear()
        with patch("dbusers.utils.connection") as conn:
            sync(self.user)

        cursor = conn.cursor.return_value.__enter__.return_value
        sql, params = cursor.execute.call_args[0]
        self.assertIn("delete_db_user", sql)
        self.assertEqual(params, [MARCUS])
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

    def test_active_backend_is_password_based(self):
        """Guards the swap point between authentication backends.

        When this changes, the admin's one-time password message goes with it.
        See requirements.md."""
        self.assertTrue(get_backend().issues_secret)


class CredentialHandoverTests(TestCase):
    """Who gets to see the password, and when.

    The point of splitting enrollment from issuing is that an administrator never learns a
    credential belonging to someone else, and that no secret has to be stored while it
    waits to be collected. These tests are what keep that property from being lost.
    """

    def setUp(self):
        Group.objects.get_or_create(name=EDITOR_GROUP)
        self.user = User.objects.create(username="marcus")
        self.user.groups.add(Group.objects.get(name=EDITOR_GROUP))

    def test_enrolling_creates_the_role_without_a_password(self):
        with patch("dbusers.utils.connection") as conn:
            record = enroll(self.user)

        cursor = conn.cursor.return_value.__enter__.return_value
        sql, params = cursor.execute.call_args[0]
        self.assertIn("create_db_user", sql)
        self.assertEqual(params, [MARCUS, None, EDITOR])
        self.assertTrue(record.awaiting_credential)

    def test_the_password_is_issued_on_the_next_login(self):
        with patch("dbusers.utils.connection"):
            enroll(self.user)

        with patch("dbusers.utils.connection") as conn:
            secret = issue_credential(self.user)

        self.assertIsNotNone(secret)
        cursor = conn.cursor.return_value.__enter__.return_value
        _, params = cursor.execute.call_args[0]
        self.assertEqual(params[0], MARCUS)
        self.assertEqual(params[1], secret, "the issued password must be the one set")

    def test_the_password_is_issued_only_once(self):
        with patch("dbusers.utils.connection"):
            enroll(self.user)
            self.assertIsNotNone(issue_credential(self.user))
            # A second login must not mint a new password, or every sign-in would
            # invalidate the one the person already saved.
            self.assertIsNone(issue_credential(self.user))

    def test_nothing_is_stored_anywhere(self):
        """The secret exists only in the return value; no field holds it."""
        with patch("dbusers.utils.connection"):
            enroll(self.user)
            secret = issue_credential(self.user)

        record = DatabaseUser.objects.get(user=self.user)
        stored = " ".join(str(value) for value in record.__dict__.values())
        self.assertNotIn(secret, stored)

    def test_someone_without_an_account_is_issued_nothing(self):
        other = User.objects.create(username="someone_else")
        with patch("dbusers.utils.connection"):
            self.assertIsNone(issue_credential(other))

    def test_reset_puts_the_account_back_in_the_waiting_state(self):
        with patch("dbusers.utils.connection"):
            enroll(self.user)
            issue_credential(self.user)

        with patch("dbusers.utils.connection") as conn:
            reset(self.user)

        cursor = conn.cursor.return_value.__enter__.return_value
        sql, params = cursor.execute.call_args[0]
        # Cleared straight away, so a leaked password stops working now rather than at the
        # person's next sign-in.
        self.assertIn("clear_db_user_password", sql)
        self.assertEqual(params, [MARCUS])
        self.assertTrue(DatabaseUser.objects.get(user=self.user).awaiting_credential)

    def test_reset_then_login_issues_a_different_password(self):
        with patch("dbusers.utils.connection"):
            enroll(self.user)
            first = issue_credential(self.user)
            reset(self.user)
            second = issue_credential(self.user)

        self.assertIsNotNone(second)
        self.assertNotEqual(first, second)


class NonStaffTests(TestCase):
    """A database account does not depend on reaching the admin.

    Someone may query the warehouse without administering anything, so the rank groups
    are what decide the account and is_staff is not consulted at all.
    """

    def setUp(self):
        Group.objects.get_or_create(name=VIEWER_GROUP)
        self.user = User.objects.create(username="marcus", is_staff=False)
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
        self.user = User.objects.create(username="marcus")
        self.user.groups.add(Group.objects.get(name=EDITOR_GROUP))
        with patch("dbusers.utils.connection"):
            enroll(self.user)

    def test_remove_drops_the_role_and_the_row(self):
        with patch("dbusers.utils.connection") as conn:
            remove(self.user)

        cursor = conn.cursor.return_value.__enter__.return_value
        sql, params = cursor.execute.call_args[0]
        self.assertIn("drop_db_user", sql)
        self.assertEqual(params, [MARCUS])
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

        self.assertTrue(record.awaiting_credential)
        self.assertEqual(record.role_name, MARCUS)
