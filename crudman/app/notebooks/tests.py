"""Unit tests for what JupyterHub asks crudman, with the database mocked.

What matters here is the boundary: which ranks reach the notebooks at all, and that a
credential is only ever issued for the caller's own account. The database function behind it
is covered against the live stack in tests/test_notebooks.py.
"""
from unittest.mock import patch

from dbusers.models import DatabaseUser
from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse
from sso.roles import GROUP_FOR_RANK

from .context_processors import NOTEBOOK_PATH
from .utils import issue_notebook_credential, may_use_notebooks, refusal


def make_user(username, group=None, **flags):
    """A Django user in one rank group."""
    user = User.objects.create_user(username=username, password="x", **flags)
    if group:
        user.groups.add(Group.objects.get_or_create(name=group)[0])
    return user


def enrolled(user):
    """The database account row a person needs before a notebook credential exists."""
    return DatabaseUser.objects.create(
        user=user,
        role_name=f"gf_{user.username}",
        group_role=GROUP_FOR_RANK["editor"],
        is_enabled=True,
        awaiting_credential=False,
    )


class RankTests(TestCase):
    """Who may open a notebook.

    Both halves of what a spawn needs, so the sidebar link and the spawn agree: writing
    models means writing the warehouse, which is the editor line the rest of the system
    draws, and the notebook's credential is a sibling of a database account that has to
    exist.
    """

    def test_editor_with_an_account_may(self):
        user = make_user("editor", GROUP_FOR_RANK["editor"])
        enrolled(user)
        self.assertTrue(may_use_notebooks(user))

    def test_admin_with_an_account_may(self):
        user = make_user("admin_user", GROUP_FOR_RANK["admin"])
        enrolled(user)
        self.assertTrue(may_use_notebooks(user))

    def test_viewer_may_not(self):
        user = make_user("viewer", GROUP_FOR_RANK["viewer"])
        enrolled(user)
        self.assertFalse(may_use_notebooks(user))

    def test_rankless_may_not(self):
        self.assertFalse(may_use_notebooks(make_user("nobody")))

    def test_superuser_with_an_account_may(self):
        """Single sign-on is what grants a group, and it may be switched off entirely."""
        user = make_user("root", is_superuser=True)
        enrolled(user)
        self.assertTrue(may_use_notebooks(user))

    def test_an_editor_without_a_database_account_may_not(self):
        """The rank alone is not enough: the notebook login is a sibling of an account
        that has to be there. Offering a link that then fails to spawn is worse than
        offering none -- which is what the deployment's superuser used to get."""
        self.assertFalse(may_use_notebooks(make_user("editor", GROUP_FOR_RANK["editor"])))

    def test_an_unclaimed_account_is_not_enough(self):
        """Until the password is issued the role cannot connect at all."""
        user = make_user("editor", GROUP_FOR_RANK["editor"])
        record = enrolled(user)
        record.awaiting_credential = True
        record.save()
        self.assertFalse(may_use_notebooks(user))

    def test_deactivated_editor_may_not(self):
        """Losing every role in the provider deactivates the account; it must close this
        door too, not only the admin's."""
        user = make_user("gone", GROUP_FOR_RANK["editor"])
        enrolled(user)
        user.is_active = False
        self.assertFalse(may_use_notebooks(user))


class RefusalTests(TestCase):
    """What a refused person is told, which is shown to them by JupyterHub.

    Each cause needs its own next step: the rule that was broken is not actionable.
    """

    def test_a_viewer_is_told_about_the_rank(self):
        self.assertIn("editor", refusal(make_user("viewer", GROUP_FOR_RANK["viewer"])))

    def test_an_editor_without_an_account_is_told_to_ask(self):
        message = refusal(make_user("editor", GROUP_FOR_RANK["editor"]))
        self.assertIn("Database access", message)

    def test_an_unclaimed_account_is_told_to_sign_in_again(self):
        user = make_user("editor", GROUP_FOR_RANK["editor"])
        record = enrolled(user)
        record.awaiting_credential = True
        record.save()
        self.assertIn("sign", refusal(user).lower())


class CredentialTests(TestCase):
    """Rotating the login a notebook server connects with."""

    def test_calls_the_database_function_with_the_persons_role(self):
        user = make_user("editor", GROUP_FOR_RANK["editor"])
        record = enrolled(user)

        with patch("notebooks.utils.connection") as connection:
            cursor = connection.cursor.return_value.__enter__.return_value
            cursor.fetchone.return_value = (f"{record.role_name}_nb",)
            login, password, role = issue_notebook_credential(user)

        sql, args = cursor.execute.call_args[0]
        self.assertIn("create_notebook_login", sql)
        self.assertEqual(args[0], record.role_name)
        self.assertEqual(args[1], password)
        self.assertEqual(login, f"{record.role_name}_nb")
        self.assertEqual(role, record.role_name, "the session assumes the person's role")

    def test_password_is_new_every_time(self):
        """A credential in a process environment is safe because it does not outlive the
        session it was made for."""
        user = make_user("editor", GROUP_FOR_RANK["editor"])
        enrolled(user)

        with patch("notebooks.utils.connection") as connection:
            connection.cursor.return_value.__enter__.return_value.fetchone.return_value = ("r",)
            first = issue_notebook_credential(user)[1]
            second = issue_notebook_credential(user)[1]

        self.assertNotEqual(first, second)

    def test_refuses_without_a_database_account(self):
        """Provisioning one is an administrator's deliberate act, so this reports."""
        user = make_user("editor", GROUP_FOR_RANK["editor"])
        with self.assertRaises(ValueError):
            issue_notebook_credential(user)

    def test_refuses_while_the_password_is_unclaimed(self):
        """The role cannot connect until the person has signed in and claimed it, so a
        notebook login built on it would be a working credential for a dead account."""
        user = make_user("editor", GROUP_FOR_RANK["editor"])
        record = enrolled(user)
        record.awaiting_credential = True
        record.save()

        with self.assertRaises(ValueError):
            issue_notebook_credential(user)


class SidebarLinkTests(TestCase):
    """The admin's link to the notebooks.

    Unfold offers two settings for this and neither works here: SIDEBAR["navigation"]
    replaces the app list with what it is given, hiding every registered model, and
    SITE_DROPDOWN renders a panel that opens only when somebody clicks the site name. So
    the navigation template is overridden, and these check the entry actually renders --
    a link that silently is not there looks exactly like one nobody clicked.
    """

    def setUp(self):
        self.url = reverse("admin:index")

    def test_an_editor_is_offered_the_link(self):
        user = make_user("editor", GROUP_FOR_RANK["editor"], is_staff=True)
        enrolled(user)
        self.client.force_login(user)

        page = self.client.get(self.url).content.decode()

        self.assertIn("Notebooks", page)
        self.assertIn(f'href="/{NOTEBOOK_PATH}/"', page)

    def test_a_viewer_is_not(self):
        user = make_user("viewer", GROUP_FOR_RANK["viewer"], is_staff=True)
        enrolled(user)
        self.client.force_login(user)

        self.assertNotIn(f'href="/{NOTEBOOK_PATH}/"', self.client.get(self.url).content.decode())

    def test_the_admin_sections_are_still_listed(self):
        """The whole reason for the template override: the app list has to survive it."""
        user = make_user("editor", GROUP_FOR_RANK["editor"], is_staff=True, is_superuser=True)
        enrolled(user)
        self.client.force_login(user)

        page = self.client.get(self.url).content.decode()

        self.assertIn("Notebooks", page)
        self.assertIn("Dropzones", page)


class WhoamiTests(TestCase):
    """The endpoint the hub authenticates against."""

    def setUp(self):
        self.url = reverse("notebooks:whoami")

    def test_a_request_through_the_proxy_is_not_answered(self):
        """A browser reaches this path too, and a page on another site could make one
        rotate somebody's credential through their cookie. Only the hub, which calls
        Django directly and so sets no forwarding header, gets an answer."""
        enrolled(make_user("editor", GROUP_FOR_RANK["editor"]))
        self.client.login(username="editor", password="x")

        response = self.client.post(self.url, headers={"x-forwarded-for": "203.0.113.7"})

        self.assertEqual(response.status_code, 404)

    def test_anonymous_is_refused(self):
        self.assertEqual(self.client.get(self.url).status_code, 401)

    def test_viewer_is_refused(self):
        make_user("viewer", GROUP_FOR_RANK["viewer"])
        self.client.login(username="viewer", password="x")
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_editor_is_identified(self):
        user = make_user("editor", GROUP_FOR_RANK["editor"])
        user.email = "editor@example.com"
        user.save()
        enrolled(user)
        self.client.login(username="editor", password="x")

        answer = self.client.get(self.url).json()

        self.assertEqual(answer["username"], "editor")
        self.assertEqual(answer["email"], "editor@example.com")
        self.assertFalse(answer["admin"])

    def test_get_issues_no_credential(self):
        """Only a spawn rotates it; a page view must not invalidate a running server."""
        enrolled(make_user("editor", GROUP_FOR_RANK["editor"]))
        self.client.login(username="editor", password="x")

        self.assertNotIn("db_password", self.client.get(self.url).json())

    def test_post_issues_the_credential_of_the_caller(self):
        user = make_user("editor", GROUP_FOR_RANK["editor"])
        enrolled(user)
        self.client.login(username="editor", password="x")

        with patch("notebooks.utils.connection") as connection:
            connection.cursor.return_value.__enter__.return_value.fetchone.return_value = (
                "gf_editor_nb",
            )
            answer = self.client.post(self.url).json()

        self.assertEqual(answer["db_user"], "gf_editor_nb")
        self.assertTrue(answer["db_password"])
        self.assertEqual(answer["db_role"], "gf_editor")

    def test_post_without_an_account_explains_itself(self):
        """Refused before the credential is even attempted, since a database account is
        half of what may_use_notebooks requires. The hub shows this to the person, so it
        has to name the next step rather than the rule."""
        make_user("editor", GROUP_FOR_RANK["editor"])
        self.client.login(username="editor", password="x")

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 403)
        self.assertIn("Database access", response.json()["detail"])

    def test_the_credential_is_never_cached(self):
        user = make_user("editor", GROUP_FOR_RANK["editor"])
        enrolled(user)
        self.client.login(username="editor", password="x")

        with patch("notebooks.utils.connection") as connection:
            connection.cursor.return_value.__enter__.return_value.fetchone.return_value = ("r",)
            response = self.client.post(self.url)

        self.assertEqual(response.headers["Cache-Control"], "no-store")
