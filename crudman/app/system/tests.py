import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse
from sso.roles import GROUP_FOR_RANK

from . import repo
from .models import Deployment

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


class SeedProjectsTest(TestCase):
    """A seed carrying example projects becomes one commit each, and each one parses.

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

    def test_there_is_one_commit_per_example_project(self):
        self.assertEqual(len(repo.log()), len(repo.SEED_PROJECTS))

    def test_each_commit_adds_the_next_project(self):
        # Oldest first, which is the order they were built in.
        for step, commit in enumerate(reversed(repo.log()), start=1):
            added = repo.SEED_PROJECTS[step - 1]
            self.assertIn(added, commit["subject"])

    def test_a_commit_carries_only_the_projects_it_has_added(self):
        oldest = repo.log()[-1]["sha"]
        listed = repo.git("ls-tree", "-r", "--name-only", oldest)

        self.assertIn(repo.SEED_PROJECTS[0], listed)
        for later in repo.SEED_PROJECTS[1:]:
            self.assertNotIn(later, listed)

    def test_the_union_names_only_the_projects_that_exist(self):
        # A branch reading a project this commit has not added would not parse, which is
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
