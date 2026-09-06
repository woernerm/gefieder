import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse
from sso.roles import GROUP_FOR_RANK

from . import repo, utils
from .forms import TenantChangeForm, TenantCreationForm
from .models import Deployment, Tenant


class TenantModelTests(TestCase):
    def test_limits_default_to_unlimited_sentinels(self):
        """Limit fields default to the values that stand for "no limit" (infinite)."""
        tenant = Tenant(name="acme")
        self.assertEqual(tenant.connection_limit, Tenant.UNLIMITED_COUNT)
        self.assertEqual(tenant.statement_timeout, Tenant.UNLIMITED_SIZE)
        self.assertEqual(tenant.work_mem, Tenant.UNLIMITED_SIZE)
        self.assertEqual(tenant.temp_file_limit, Tenant.UNLIMITED_SIZE)

    def test_str_is_display_name(self):
        self.assertEqual(str(Tenant(name="acme", display_name="Acme")), "Acme")

    def test_str_falls_back_to_slug_without_display_name(self):
        # Tenants created outside crudman have no display name.
        self.assertEqual(str(Tenant(name="project_a")), "project_a")


class SlugifyTenantNameTests(TestCase):
    def test_lowercases_and_replaces_spaces(self):
        # The headline case: a valid PostgreSQL identifier out of a human name.
        self.assertEqual(utils.slugify_tenant_name("Project A"), "project_a")

    def test_collapses_separators_and_strips_edges(self):
        self.assertEqual(utils.slugify_tenant_name("  Customer A / Project  "), "customer_a_project")

    def test_prefixes_leading_digit(self):
        # PostgreSQL identifiers may not start with a digit.
        self.assertEqual(utils.slugify_tenant_name("3M"), "t_3m")

    def test_empty_when_no_usable_characters(self):
        self.assertEqual(utils.slugify_tenant_name("!!!"), "")


class CreateTenantUtilTests(TestCase):
    @patch("system.utils.connection")
    def test_calls_database_function_with_same_parameters(self, connection):
        cursor = connection.cursor.return_value.__enter__.return_value

        result = utils.create_tenant("acme", "supersecret", "Acme")

        self.assertTrue(result)
        cursor.execute.assert_called_once_with(
            "SELECT create_tenant(%s, %s, %s)", ["acme", "supersecret", "Acme"]
        )

    @patch("system.utils.connection")
    def test_returns_false_on_failure(self, connection):
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.execute.side_effect = Exception("boom")

        self.assertFalse(utils.create_tenant("acme", "supersecret"))


class SetTenantLimitsUtilTests(TestCase):
    @patch("system.utils.connection")
    def test_none_limits_map_to_unlimited_sentinels(self, connection):
        cursor = connection.cursor.return_value.__enter__.return_value

        utils.set_tenant_limits("acme")

        # Unlimited connections is -1; unlimited memory/time is "0" in PostgreSQL.
        cursor.execute.assert_called_once_with(
            "SELECT set_tenant_limits(%s, %s, %s, %s, %s)",
            ["acme", -1, "0", "0", "0"],
        )

    @patch("system.utils.connection")
    def test_explicit_limits_are_forwarded(self, connection):
        cursor = connection.cursor.return_value.__enter__.return_value

        utils.set_tenant_limits("acme", 5, "5min", "256MB", "1GB")

        cursor.execute.assert_called_once_with(
            "SELECT set_tenant_limits(%s, %s, %s, %s, %s)",
            ["acme", 5, "5min", "256MB", "1GB"],
        )


class DeleteTenantUtilTests(TestCase):
    @patch("system.utils.connection")
    def test_calls_database_function(self, connection):
        cursor = connection.cursor.return_value.__enter__.return_value

        self.assertTrue(utils.delete_tenant("acme"))
        cursor.execute.assert_called_once_with("SELECT delete_tenant(%s)", ["acme"])


class GetTenantsUtilTests(TestCase):
    @patch("system.utils.connection")
    def test_builds_tenant_instances_from_rows(self, connection):
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = [
            ("acme", "Acme", 5, "5min", "256MB", "1GB"),
            ("globex", None, -1, "0", "0", "0"),
        ]

        tenants = utils.get_tenants()

        self.assertEqual([t.name for t in tenants], ["acme", "globex"])

        acme = tenants[0]
        self.assertEqual(acme.display_name, "Acme")
        # A schema with no comment reads as None and becomes an empty display name.
        self.assertEqual(tenants[1].display_name, "")
        self.assertEqual(acme.connection_limit, 5)
        self.assertEqual(acme.statement_timeout, "5min")
        self.assertEqual(acme.work_mem, "256MB")
        self.assertEqual(acme.temp_file_limit, "1GB")

        # The sentinels are kept as they are, the same the add form uses.
        globex = tenants[1]
        self.assertEqual(globex.connection_limit, Tenant.UNLIMITED_COUNT)
        self.assertEqual(globex.statement_timeout, Tenant.UNLIMITED_SIZE)
        self.assertEqual(globex.work_mem, Tenant.UNLIMITED_SIZE)
        self.assertEqual(globex.temp_file_limit, Tenant.UNLIMITED_SIZE)

    @patch("system.utils.connection")
    def test_unset_catalog_values_become_unlimited_sentinels(self, connection):
        # A role with no per-role settings (NULLs) still reads as "no limit".
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = [("acme", None, None, None, None, None)]

        tenant = utils.get_tenants()[0]
        self.assertIsInstance(tenant, Tenant)
        self.assertEqual(tenant.connection_limit, Tenant.UNLIMITED_COUNT)
        self.assertEqual(tenant.statement_timeout, Tenant.UNLIMITED_SIZE)
        self.assertEqual(tenant.work_mem, Tenant.UNLIMITED_SIZE)
        self.assertEqual(tenant.temp_file_limit, Tenant.UNLIMITED_SIZE)


class SyncTenantsUtilTests(TestCase):
    """sync_tenants mirrors the live PostgreSQL tenants into the cache table."""

    @patch("system.utils.get_tenants")
    def test_inserts_updates_and_removes_rows(self, get_tenants):
        # A stale row that no longer exists in PostgreSQL.
        Tenant.objects.create(name="old", connection_limit=1)

        get_tenants.return_value = [
            Tenant(
                name="acme",
                display_name="Acme",
                connection_limit=5,
                statement_timeout="5min",
            ),
        ]
        utils.sync_tenants()

        self.assertEqual(list(Tenant.objects.values_list("name", flat=True)), ["acme"])
        acme = Tenant.objects.get(name="acme")
        # The human name from the catalog is mirrored into the cache too.
        self.assertEqual(acme.display_name, "Acme")
        self.assertEqual(acme.connection_limit, 5)
        self.assertEqual(acme.statement_timeout, "5min")


class TenantFormTests(TestCase):
    def test_creation_form_requires_password(self):
        form = TenantCreationForm(data={"display_name": "Acme"})
        self.assertIn("password", form.errors)

    def test_creation_form_derives_slug_from_display_name(self):
        # "Project A" -> the slug "project_a" used as the role and bronze schema name.
        form = TenantCreationForm(
            data={"display_name": "Project A", "password": "supersecret"}
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.instance.name, "project_a")

    def test_creation_form_rejects_unsluggable_name(self):
        form = TenantCreationForm(
            data={"display_name": "!!!", "password": "supersecret"}
        )
        self.assertIn("display_name", form.errors)

    def test_creation_form_rejects_duplicate_slug(self):
        Tenant.objects.create(name="project_a", display_name="Project A")
        form = TenantCreationForm(
            data={"display_name": "Project A", "password": "supersecret"}
        )
        self.assertIn("display_name", form.errors)

    def test_change_form_name_is_disabled(self):
        form = TenantChangeForm(instance=Tenant(name="acme"))
        self.assertTrue(form.fields["name"].disabled)


class TenantAdminTests(TestCase):
    """The admin drives PostgreSQL, caching a row only once that has succeeded."""

    def setUp(self):
        from django.contrib import admin

        self.admin = admin.site._registry[Tenant]
        self.request = MagicMock()

    @patch("system.admin.set_tenant_limits", return_value=True)
    @patch("system.admin.create_tenant", return_value=True)
    def test_save_model_creates_tenant_and_caches_row(self, create, set_limits):
        obj = Tenant(name="acme", display_name="Acme", connection_limit=5)
        form = MagicMock()
        form.cleaned_data = {"password": "supersecret"}

        self.admin.save_model(self.request, obj, form, change=False)

        create.assert_called_once_with("acme", "supersecret", "Acme")
        # The unset size limits keep their unlimited-sentinel defaults.
        set_limits.assert_called_once_with("acme", 5, "0", "0", "0")
        # The cache row is written only after the database functions succeed.
        self.assertTrue(Tenant.objects.filter(name="acme").exists())

    @patch("system.admin.set_tenant_limits", return_value=True)
    @patch("system.admin.create_tenant", return_value=False)
    def test_save_model_does_not_cache_when_create_fails(self, create, set_limits):
        obj = Tenant(name="acme")
        form = MagicMock()
        form.cleaned_data = {"password": "supersecret"}

        self.admin.save_model(self.request, obj, form, change=False)

        set_limits.assert_not_called()
        self.assertFalse(Tenant.objects.filter(name="acme").exists())

    @patch("system.admin.set_tenant_display_name", return_value=True)
    @patch("system.admin.set_tenant_limits", return_value=True)
    @patch("system.admin.create_tenant", return_value=True)
    def test_save_model_on_edit_updates_name_and_limits(
        self, create, set_limits, set_name
    ):
        obj = Tenant.objects.create(
            name="acme", display_name="Acme", connection_limit=5
        )
        obj.connection_limit = 10
        obj.display_name = "Acme Corp"
        form = MagicMock()
        form.cleaned_data = {}

        self.admin.save_model(self.request, obj, form, change=True)

        # An edit does not re-create the tenant, but does propagate the renamed display
        # name to PostgreSQL (the schema comment) and apply the limits.
        create.assert_not_called()
        set_name.assert_called_once_with("acme", "Acme Corp")
        set_limits.assert_called_once_with("acme", 10, "0", "0", "0")
        self.assertEqual(Tenant.objects.get(name="acme").connection_limit, 10)

    @patch("system.admin.delete_tenant", return_value=True)
    def test_delete_model_calls_delete_tenant_and_removes_row(self, delete):
        obj = Tenant.objects.create(name="acme")
        self.admin.delete_model(self.request, obj)
        delete.assert_called_once_with("acme")
        self.assertFalse(Tenant.objects.filter(name="acme").exists())

    @patch("system.admin.delete_tenant", return_value=False)
    def test_delete_model_keeps_row_when_delete_fails(self, delete):
        obj = Tenant.objects.create(name="acme")
        self.admin.delete_model(self.request, obj)
        self.assertTrue(Tenant.objects.filter(name="acme").exists())


class TenantAdminLimitDisplayTests(TestCase):
    """The changelist renders the "no limit" sentinels as "infinite"."""

    def setUp(self):
        from django.contrib import admin

        self.admin = admin.site._registry[Tenant]

    def test_unlimited_sentinels_render_as_infinite(self):
        tenant = Tenant(
            name="acme",
            connection_limit=Tenant.UNLIMITED_COUNT,
            statement_timeout=Tenant.UNLIMITED_SIZE,
            work_mem=Tenant.UNLIMITED_SIZE,
            temp_file_limit=Tenant.UNLIMITED_SIZE,
        )
        self.assertEqual(self.admin.connection_limit_display(tenant), "infinite")
        self.assertEqual(self.admin.statement_timeout_display(tenant), "infinite")
        self.assertEqual(self.admin.work_mem_display(tenant), "infinite")
        self.assertEqual(self.admin.temp_file_limit_display(tenant), "infinite")

    def test_real_limits_render_unchanged(self):
        tenant = Tenant(
            name="acme",
            connection_limit=5,
            statement_timeout="5min",
            work_mem="256MB",
            temp_file_limit="1GB",
        )
        self.assertEqual(self.admin.connection_limit_display(tenant), 5)
        self.assertEqual(self.admin.statement_timeout_display(tenant), "5min")
        self.assertEqual(self.admin.work_mem_display(tenant), "256MB")
        self.assertEqual(self.admin.temp_file_limit_display(tenant), "1GB")


class TenantAdminViewTests(TestCase):
    """The admin pages render, end to end, driven by the database functions."""

    def setUp(self):
        admin_user = User.objects.create_superuser("admin", "a@example.com", "password")
        self.client.force_login(admin_user)

    @patch("system.admin.sync_tenants")
    def test_changelist_syncs_and_lists_tenants(self, sync):
        # Seeded directly, in place of the sync from PostgreSQL.
        Tenant.objects.create(name="acme")
        response = self.client.get(reverse("admin:system_tenant_changelist"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "acme")
        sync.assert_called_once()

    @patch("system.admin.set_tenant_limits", return_value=True)
    @patch("system.admin.create_tenant", return_value=True)
    def test_add_view_calls_create_tenant(self, create, set_limits):
        response = self.client.post(
            reverse("admin:system_tenant_add"),
            {
                "display_name": "Project A",
                "password": "supersecret",
                "connection_limit": "",
                "statement_timeout": "",
                "work_mem": "",
                "temp_file_limit": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        # The derived slug reaches create_tenant along with the human name.
        create.assert_called_once_with("project_a", "supersecret", "Project A")
        self.assertTrue(Tenant.objects.filter(name="project_a").exists())

    @patch("system.admin.delete_tenant", return_value=True)
    @patch("system.admin.sync_tenants")
    def test_delete_view_calls_delete_tenant(self, sync, delete):
        Tenant.objects.create(name="acme")
        response = self.client.post(
            reverse("admin:system_tenant_delete", args=["acme"]), {"post": "yes"}
        )
        self.assertEqual(response.status_code, 302)
        delete.assert_called_once_with("acme")
        self.assertFalse(Tenant.objects.filter(name="acme").exists())


# ---------------------------------------------------------------------------------------
# The models repository, exercised against a real git binary in a temporary directory.
#
# Nothing here stubs git. The whole point is that git decides what a commit is, what a
# branch reaches and what a checkout leaves behind, so a stubbed one would test the stub.
# A bare repository in a temporary directory is cheap enough to make that unnecessary.
# ---------------------------------------------------------------------------------------

SEED_PYPROJECT = '[project]\nname = "sqlmesh-service"\ndependencies = ["sqlmesh"]\n'


class RepositoryTestCase(TestCase):
    """A models volume of this test's own, with the seed the crudman image would carry."""

    def setUp(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)

        seed = root / "seed"
        (seed / repo.PROJECT / "models").mkdir(parents=True)
        (seed / repo.PROJECT / "pyproject.toml").write_text(SEED_PYPROJECT)
        (seed / repo.PROJECT / "models" / "example.sql").write_text("SELECT 1")

        models = root / "models"
        models.mkdir()

        self.enterContext(patch.object(repo, "SEED", seed))
        self.enterContext(patch.object(repo, "MODELS_DIR", models))
        self.enterContext(patch.object(repo, "ORIGIN", str(models / "origin.git")))
        self.enterContext(patch.object(repo, "DEPLOYED", models / "deployed"))
        self.enterContext(patch.object(repo, "WORKSPACES", models / "workspaces"))
        self.enterContext(patch.object(repo, "MARKER", models / "deployed.sha"))

    def commit(self, message, changes=None, when=None):
        """Add a commit to the origin's branch, the way a developer's push would.

        Args:
            message: The commit subject.
            changes: Paths relative to the repository root, mapped to their new contents.
            when: An ISO-8601 stamp for both of the commit's dates, for the tests that
                need two commits to share a second.

        Returns:
            The new commit's sha.
        """
        work = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, work, True)
        repo.git("clone", repo.ORIGIN, str(work), cwd=repo.MODELS_DIR)

        for name, content in (changes or {"note.txt": message}).items():
            path = work / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)

        repo.git("add", "--all", cwd=work)
        repo.git(
            "-c", "user.name=Jean Dupont", "-c", "user.email=jean@example.com",
            "commit", "--message", message, cwd=work,
            env={"GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when} if when else None,
        )
        repo.git("push", "origin", repo.BRANCH, cwd=work)
        sha = repo.git("rev-parse", "HEAD", cwd=work)
        repo.fetch()
        return sha


class SeedTenantsTest(TestCase):
    """A seed carrying example tenants becomes one commit each, and each one parses.

    The commits are what the versions page has to demonstrate on the first day, so a fresh
    installation is not asked to wait for somebody to push twice.
    """

    def setUp(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)

        seed = root / "seed"
        for project in repo.SEED_PROJECTS:
            (seed / repo.PROJECT / "models" / "bronze" / project).mkdir(parents=True)
            (seed / repo.PROJECT / "models" / "bronze" / project / "issues.sql").write_text(
                f"MODEL (name bronze_{project}.issues);\nSELECT 1 AS tenant_id"
            )
        (seed / repo.PROJECT / "models" / "silver").mkdir(parents=True)
        (seed / repo.PROJECT / "models" / "silver" / "issues.sql").write_text(
            "MODEL (\n  name silver.issues\n);\n\n"
            + "\nUNION ALL\n".join(
                f"SELECT tenant_id FROM bronze_{project}.issues"
                for project in repo.SEED_PROJECTS
            )
        )
        (seed / repo.PROJECT / "pyproject.toml").write_text("[project]\n")

        models = root / "models"
        models.mkdir()
        self.enterContext(patch.object(repo, "SEED", seed))
        self.enterContext(patch.object(repo, "MODELS_DIR", models))
        self.enterContext(patch.object(repo, "ORIGIN", str(models / "origin.git")))
        self.enterContext(patch.object(repo, "DEPLOYED", models / "deployed"))
        self.enterContext(patch.object(repo, "WORKSPACES", models / "workspaces"))
        self.enterContext(patch.object(repo, "MARKER", models / "deployed.sha"))
        repo.ensure()

    def union(self, sha):
        """The harmonizing model as it stands at one commit."""
        return repo.git("show", f"{sha}:{repo.PROJECT}/models/silver/issues.sql")

    def test_there_is_one_commit_per_example_tenant(self):
        self.assertEqual(len(repo.log()), len(repo.SEED_PROJECTS))

    def test_each_commit_adds_the_next_tenant(self):
        # Oldest first, which is the order they were built in.
        for step, commit in enumerate(reversed(repo.log()), start=1):
            added = repo.SEED_PROJECTS[step - 1]
            self.assertIn(added, commit["subject"])

    def test_a_commit_carries_only_the_tenants_it_has_added(self):
        oldest = repo.log()[-1]["sha"]
        listed = repo.git("ls-tree", "-r", "--name-only", oldest)

        self.assertIn(repo.SEED_PROJECTS[0], listed)
        for later in repo.SEED_PROJECTS[1:]:
            self.assertNotIn(later, listed)

    def test_the_union_names_only_the_tenants_that_exist(self):
        # A branch reading a tenant this commit has not added would not parse, which is
        # what stops an earlier version from being a broken one.
        oldest, newest = repo.log()[-1]["sha"], repo.log()[0]["sha"]

        self.assertEqual(self.union(oldest).count("UNION ALL"), 0)
        self.assertEqual(
            self.union(newest).count("UNION ALL"), len(repo.SEED_PROJECTS) - 1
        )
        for later in repo.SEED_PROJECTS[1:]:
            self.assertNotIn(later, self.union(oldest))

    def test_the_commits_are_stamped_apart(self):
        # Made in the same second otherwise, which leaves their order a tie for anything
        # reading the history back -- and the page is a list in date order.
        stamps = [commit["when"] for commit in repo.log()]
        self.assertEqual(len(set(stamps)), len(stamps))

    def test_the_newest_commit_comes_first(self):
        stamps = [commit["when"] for commit in repo.log()]
        self.assertEqual(stamps, sorted(stamps, reverse=True))

    def test_the_model_block_survives_the_narrowing(self):
        oldest = repo.log()[-1]["sha"]
        self.assertIn("MODEL (", self.union(oldest))
        self.assertIn("name silver.issues", self.union(oldest))


class SeedingTest(RepositoryTestCase):
    """What a system with no git host of its own starts life as."""

    def test_the_first_start_creates_and_clones_a_repository(self):
        repo.ensure()

        self.assertTrue((Path(repo.ORIGIN) / "HEAD").exists())
        self.assertTrue((repo.DEPLOYED / repo.PROJECT / "models" / "example.sql").exists())
        self.assertEqual(len(repo.log()), 1)

    def test_the_clone_can_be_developed_without_this_repository(self):
        repo.ensure()

        # server.env is what makes that true: it carries the address, port, database and
        # role prefix that sqlmesh/config.py would otherwise read from gefieder's own
        # env files, which a clone of the models does not have.
        settings = (repo.DEPLOYED / repo.PROJECT / "server.env").read_text()
        self.assertIn("SQLMESH_HOST=", settings)
        self.assertIn("SQLMESH_DATABASE=", settings)
        self.assertIn("SQLMESH_USER_PREFIX=", settings)

    def test_starting_again_changes_nothing(self):
        repo.ensure()
        first = repo.head_of_main()
        repo.ensure()
        self.assertEqual(repo.head_of_main(), first)

    def test_a_workspace_directory_is_kept_clear_of_the_deployed_tree(self):
        repo.ensure()
        self.assertTrue(repo.WORKSPACES.is_dir())
        self.assertNotEqual(repo.WORKSPACES, repo.DEPLOYED)


class PollTest(RepositoryTestCase):
    """Deploying what the branch points at, and knowing when not to."""

    def test_the_first_poll_deploys_the_branch(self):
        deployment = repo.poll()

        self.assertEqual(deployment.sha, repo.head_of_main())
        self.assertEqual(deployment.status, Deployment.CHECKING_OUT)
        self.assertFalse(deployment.pinned)
        self.assertEqual(repo.MARKER.read_text().strip(), deployment.sha)

    def test_polling_again_deploys_nothing(self):
        repo.poll()
        self.assertIsNone(repo.poll())
        self.assertEqual(Deployment.objects.count(), 1)

    def test_a_push_is_deployed(self):
        repo.poll()
        pushed = self.commit("Add a gold model")

        deployment = repo.poll()

        self.assertEqual(deployment.sha, pushed)
        self.assertEqual(repo.deployed_sha(), pushed)


class PinTest(RepositoryTestCase):
    """A person choosing an older version, and what takes it back."""

    def setUp(self):
        super().setUp()
        repo.poll()
        self.first = repo.deployed_sha()
        self.second = self.commit("Change a model")
        repo.poll()

    def test_an_older_version_can_be_put_back(self):
        repo.deploy(self.first, pinned=True)

        self.assertEqual(repo.deployed_sha(), self.first)
        self.assertTrue((repo.DEPLOYED / repo.PROJECT / "models" / "example.sql").exists())

    def test_the_poll_leaves_a_pinned_version_alone(self):
        repo.deploy(self.first, pinned=True)

        self.assertIsNone(repo.poll())
        self.assertEqual(repo.deployed_sha(), self.first)

    def test_a_push_supersedes_a_pinned_version(self):
        repo.deploy(self.first, pinned=True)
        pushed = self.commit("Fix the model properly")

        deployment = repo.poll()

        self.assertEqual(deployment.sha, pushed)
        self.assertEqual(repo.deployed_sha(), pushed)


class DependencyTest(RepositoryTestCase):
    """A commit the installed engine cannot run is refused rather than checked out."""

    def setUp(self):
        super().setUp()
        repo.poll()
        self.deployed = repo.deployed_sha()

    def test_a_changed_pyproject_is_refused(self):
        changed = self.commit(
            "Add a dependency",
            {f"{repo.PROJECT}/pyproject.toml": SEED_PYPROJECT + 'extra = ["pandas"]\n'},
        )

        deployment = repo.poll()

        self.assertEqual(deployment.sha, changed)
        self.assertEqual(deployment.status, Deployment.FAILED)
        self.assertIn("pyproject.toml", deployment.message)
        # The refusal is the point: the working tree still holds what was running.
        self.assertEqual(repo.deployed_sha(), self.deployed)

    def test_a_refusal_is_reported_once(self):
        self.commit(
            "Add a dependency",
            {f"{repo.PROJECT}/pyproject.toml": SEED_PYPROJECT + 'extra = ["pandas"]\n'},
        )
        repo.poll()

        self.assertIsNone(repo.poll())
        self.assertEqual(Deployment.objects.count(), 2)

    def test_a_commit_that_only_changes_models_is_deployed(self):
        changed = self.commit(
            "Add a column",
            {f"{repo.PROJECT}/models/example.sql": "SELECT 1, 2"},
        )

        deployment = repo.poll()

        self.assertEqual(deployment.status, Deployment.CHECKING_OUT)
        self.assertEqual(repo.deployed_sha(), changed)


class BranchTest(RepositoryTestCase):
    """Only history that was pushed to the branch may be deployed."""

    def test_a_commit_on_the_branch_is_accepted(self):
        repo.poll()
        self.assertTrue(repo.is_on_branch(repo.head_of_main()))

    def test_an_unknown_commit_is_not(self):
        repo.poll()
        self.assertFalse(repo.is_on_branch("0" * 40))


class VersionsPageTest(RepositoryTestCase):
    """Who reaches the page, and who may change what is running.

    It lives in the admin under System rather than beside the documentation, so it is not
    open from the viewer rank up: every rank is staff, and the rank that may change things
    is what the page takes.
    """

    def setUp(self):
        super().setUp()
        repo.poll()
        self.versions = reverse("admin:system_deployment_changelist")
        self.deploy = reverse("admin:system_deployment_deploy")
        self.older = repo.deployed_sha()
        self.commit("A newer model")
        repo.poll()

    def _user(self, name, rank):
        user = User.objects.create_user(name, password="x", is_staff=True)
        group, _ = Group.objects.get_or_create(name=GROUP_FOR_RANK[rank])
        user.groups.add(group)
        self.client.force_login(user)
        return user

    def test_an_anonymous_visitor_is_sent_to_the_login_page(self):
        response = self.client.get(self.versions)
        self.assertEqual(response.status_code, 302)

    def test_a_viewer_may_not_reach_it(self):
        self._user("jean", "viewer")

        self.assertEqual(self.client.get(self.versions).status_code, 403)

    def test_a_viewer_may_not_deploy(self):
        self._user("jean", "viewer")

        self.client.post(self.deploy, {"sha": self.older})

        self.assertNotEqual(repo.deployed_sha(), self.older)

    def test_a_deployment_still_running_is_marked_as_such(self):
        self._user("paul", "editor")

        page = self.client.get(self.versions).content.decode()

        # The spinner and the reload that stops it: the engine closes the row from another
        # container, so a page rendered while it works would otherwise sit there forever
        # claiming the deployment is still applying. Matched on the label rather than on
        # the animation class, which Unfold's own layout also uses.
        self.assertIn('aria-label="Applying"', page)
        self.assertIn('http-equiv="refresh"', page)

    def test_the_page_names_the_step_that_is_running(self):
        self._user("paul", "editor")

        for status in (Deployment.CHECKING_OUT, Deployment.TRANSFORMING,
                       Deployment.DOCUMENTING):
            with self.subTest(status=status):
                Deployment.objects.update(status=status)

                page = self.client.get(self.versions).content.decode()

                # The wait is three pieces of work; a person watching should see which.
                self.assertIn(dict(Deployment.STATUSES)[status], page)
                self.assertIn('aria-label="Applying"', page)

    def test_a_finished_deployment_neither_spins_nor_reloads(self):
        self._user("paul", "editor")
        Deployment.objects.update(status=Deployment.SUCCEEDED)

        page = self.client.get(self.versions).content.decode()

        self.assertNotIn('aria-label="Applying"', page)
        self.assertNotIn('http-equiv="refresh"', page)

    def test_the_newest_commit_is_marked_as_both_latest_and_deployed(self):
        self._user("paul", "editor")

        page = self.client.get(self.versions).content.decode()

        # The two say different things -- where the branch points, and what production is
        # doing -- and the newest commit is usually both.
        self.assertIn("Latest", page)
        self.assertIn(Deployment.objects.first().get_status_display(), page)

    def test_a_failed_deployment_says_why(self):
        self._user("paul", "editor")
        Deployment.objects.update(status=Deployment.FAILED, message="the plan did not run")

        page = self.client.get(self.versions).content.decode()

        # The only prose the page carries, and only a failure produces it.
        self.assertIn("the plan did not run", page)

    def test_an_editor_sees_the_history_and_may_put_a_version_back(self):
        self._user("paul", "editor")

        page = self.client.get(self.versions).content.decode()
        self.assertIn("A newer model", page)
        self.assertIn("Use this version", page)

        self.client.post(self.deploy, {"sha": self.older})

        self.assertEqual(repo.deployed_sha(), self.older)
        self.assertTrue(Deployment.objects.first().pinned)

    def test_a_commit_that_is_not_on_the_branch_is_refused(self):
        self._user("paul", "editor")
        deployed = repo.deployed_sha()

        self.client.post(self.deploy, {"sha": "0" * 40})

        self.assertEqual(repo.deployed_sha(), deployed)


class StrandedVersionTest(RepositoryTestCase):
    """A commit can leave the branch while it is still what production runs.

    A force-push or a rebase on the git host does it. The page is the list of versions, so
    the one that is running has to be on it whatever the branch says; a reader who cannot
    see what is deployed cannot put anything else back either.
    """

    def force_push(self, sha):
        """Move the branch back to an earlier commit, as a rebase on the host would."""
        work = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, work, True)
        repo.git("clone", repo.ORIGIN, str(work), cwd=repo.MODELS_DIR)
        repo.git("reset", "--hard", sha, cwd=work)
        repo.git("push", "--force", "origin", repo.BRANCH, cwd=work)
        repo.fetch()

    def test_the_deployed_commit_is_listed_after_the_branch_moves_away(self):
        repo.poll()
        first = repo.deployed_sha()
        stranded = self.commit("A change that is later rebased away")
        repo.poll()

        self.force_push(first)

        listed = [commit["sha"] for commit in repo.log()]
        self.assertEqual(repo.deployed_sha(), stranded)
        self.assertIn(stranded, listed)
        self.assertIn(first, listed)

    def test_a_commit_is_listed_once_when_the_branch_does_reach_it(self):
        repo.poll()

        listed = [commit["sha"] for commit in repo.log()]

        self.assertEqual(len(listed), len(set(listed)))


class OrderTest(RepositoryTestCase):
    """The page is a list of versions in date order, so the list has to be one."""

    def test_commits_made_in_the_same_second_still_have_an_order(self):
        repo.poll()
        # One timestamp for all of them, which is what a script pushing several at once
        # produces and what git's own ordering leaves ambiguous.
        stamp = "2026-01-01T12:00:00+00:00"
        for subject in ("First", "Second", "Third"):
            self.commit(subject, when=stamp)

        first = [commit["sha"] for commit in repo.log()]
        second = [commit["sha"] for commit in repo.log()]

        self.assertEqual(first, second)

    def test_the_newest_commit_comes_first(self):
        repo.poll()
        self.commit("A later change")

        stamps = [commit["when"] for commit in repo.log()]

        self.assertEqual(stamps, sorted(stamps, reverse=True))


class LostOriginTest(RepositoryTestCase):
    """A repository that has gone missing is reported, never quietly recreated."""

    def test_the_history_is_not_replaced_by_the_shipped_models(self):
        repo.poll()
        deployed = repo.deployed_sha()
        self.commit("Something worth keeping")
        repo.poll()
        shutil.rmtree(repo.ORIGIN)

        with self.assertRaises(repo.GitError):
            repo.poll()

        # Seeding again would have made this a one-commit repository of examples.
        self.assertNotEqual(repo.deployed_sha(), deployed)
        self.assertEqual(repo.MARKER.read_text().strip(), repo.deployed_sha())
