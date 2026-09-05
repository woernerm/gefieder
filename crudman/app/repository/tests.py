"""The models repository, exercised against a real git binary in a temporary directory.

Nothing here stubs git. The whole point of the app is that git decides what a commit is,
what a branch reaches and what a checkout leaves behind, so a stubbed one would test the
stub. A bare repository in a temporary directory is cheap enough to make that unnecessary.
"""

import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse
from sso.roles import GROUP_FOR_RANK

from . import repo
from .models import Deployment

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

    def commit(self, message, changes=None):
        """Add a commit to the origin's branch, the way a developer's push would.

        Args:
            message: The commit subject.
            changes: Paths relative to the repository root, mapped to their new contents.

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
        )
        repo.git("push", "origin", repo.BRANCH, cwd=work)
        sha = repo.git("rev-parse", "HEAD", cwd=work)
        repo.fetch()
        return sha


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
        self.assertEqual(deployment.status, Deployment.PENDING)
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

        self.assertEqual(deployment.status, Deployment.PENDING)
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
    """Who sees the history, and who may change what is running."""

    def setUp(self):
        super().setUp()
        repo.poll()
        self.versions = reverse("repository:versions")
        self.deploy = reverse("repository:deploy")
        self.older = repo.deployed_sha()
        self.commit("A newer model")
        repo.poll()

    def _user(self, name, rank):
        user = User.objects.create_user(name, password="x", is_staff=True)
        group, _ = Group.objects.get_or_create(name=GROUP_FOR_RANK[rank])
        user.groups.add(group)
        self.client.force_login(user)
        return user

    def test_a_viewer_sees_the_history(self):
        self._user("jean", "viewer")

        page = self.client.get(self.versions).content.decode()

        self.assertIn("A newer model", page)
        self.assertNotIn("Use this version", page)

    def test_an_anonymous_visitor_is_sent_to_the_login_page(self):
        response = self.client.get(self.versions)
        self.assertEqual(response.status_code, 302)

    def test_a_viewer_may_not_deploy(self):
        self._user("jean", "viewer")

        self.client.post(self.deploy, {"sha": self.older})

        self.assertNotEqual(repo.deployed_sha(), self.older)

    def test_an_editor_may_put_a_version_back(self):
        self._user("paul", "editor")

        page = self.client.get(self.versions).content.decode()
        self.assertIn("Use this version", page)

        self.client.post(self.deploy, {"sha": self.older})

        self.assertEqual(repo.deployed_sha(), self.older)
        self.assertTrue(Deployment.objects.first().pinned)

    def test_a_commit_that_is_not_on_the_branch_is_refused(self):
        self._user("paul", "editor")
        deployed = repo.deployed_sha()

        self.client.post(self.deploy, {"sha": "0" * 40})

        self.assertEqual(repo.deployed_sha(), deployed)


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
