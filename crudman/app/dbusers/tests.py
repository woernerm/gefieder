"""Unit tests for the provisioning logic, with the database mocked.

What is tested here is what the database is asked for: the rank a person's groups earn,
the role name derived from their username, and the reconciliation on every login. The
functions themselves are covered against the live stack in tests/test_db_users.py, the
admin switch in sso/tests.py.
"""
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.test import TestCase
from sso.roles import GROUP_FOR_RANK

from .backends import ScramBackend, get_backend
from .models import DatabaseUser
from .utils import (
    USER_PREFIX,
    db_role_for_user,
    enroll,
    issue_credential,
    remove,
    reset,
    role_name_for,
    sync,
    unmanaged_role,
)

# Derived from the configured prefixes, not the literals they happen to produce at their
# defaults, which would only assert that buildtime.env is unchanged.
VIEWER = VIEWER_GROUP = GROUP_FOR_RANK["viewer"]
EDITOR = EDITOR_GROUP = GROUP_FOR_RANK["editor"]
ADMIN = ADMIN_GROUP = GROUP_FOR_RANK["admin"]
JDOE = f"{USER_PREFIX}jdoe"


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
            awaiting_credential=False,
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

    def test_active_backend_is_password_based(self):
        """Guards the swap point: the admin's one-time password message goes with it."""
        self.assertTrue(get_backend().issues_secret)


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
        with patch("dbusers.utils.connection") as conn:
            record = enroll(self.user)

        cursor = conn.cursor.return_value.__enter__.return_value
        sql, params = cursor.execute.call_args[0]
        self.assertIn("create_db_user", sql)
        self.assertEqual(params, [JDOE, None, EDITOR])
        self.assertTrue(record.awaiting_credential)

    def test_the_password_is_issued_on_the_next_login(self):
        with patch("dbusers.utils.connection"):
            enroll(self.user)

        with patch("dbusers.utils.connection") as conn:
            secret = issue_credential(self.user)

        self.assertIsNotNone(secret)
        cursor = conn.cursor.return_value.__enter__.return_value
        _, params = cursor.execute.call_args[0]
        self.assertEqual(params[0], JDOE)
        self.assertEqual(params[1], secret, "the issued password must be the one set")

    def test_the_password_is_issued_only_once(self):
        with patch("dbusers.utils.connection"):
            enroll(self.user)
            self.assertIsNotNone(issue_credential(self.user))
            # A new password on every sign-in would invalidate the saved one.
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
        # Cleared straight away, so a leaked password stops working now.
        self.assertIn("clear_db_user_password", sql)
        self.assertEqual(params, [JDOE])
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

        self.assertTrue(record.awaiting_credential)
        self.assertEqual(record.role_name, JDOE)
