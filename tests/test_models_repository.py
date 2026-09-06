"""The models a deployment runs, end to end against the running stack.

The engine no longer carries the SQLMesh project in its image. crudman keeps a repository
on the models volume, checks a commit out and the engine plans it, so shipping a model is a
push rather than a release. What has to hold is that this works with nobody logged in: a
commit on the branch reaches the database, the documentation pages and the versions page on
its own, and a commit that would break the engine reaches none of them.

The pushes below are made from inside the crudman container, which is where the repository
and a git binary both are, so they arrive exactly as a developer's push would.
"""
import time

import pytest

from conftest import CRUDMAN_PATH, podman

MODELS_DIR = "/var/lib/app/models"
PROJECT = "sqlmesh"

DEADLINE = 180
"""Seconds a deployment gets: a poll interval, a plan of every model, and a slow host."""

SEED_PROJECTS = ("project_a", "project_b", "project_c")
"""The example tenants the seed ships, one commit each, oldest first.

Spelled out because a parameterized test needs its cases at collection time, when the stack
has not been asked anything yet. crudman/app/system/repo.py holds the list this is a copy
of, and test_there_is_a_history_to_move_through_on_the_first_day is what fails when the two
stop agreeing: every commit's subject names the tenant it added.
"""


def crudman(script):
    """Run a shell script inside the crudman container and return its output."""
    return podman("exec", "crudman", "sh", "-c", script)


def deployed_sha():
    """The commit crudman published as deployed, which is what the engine watches."""
    return crudman(f"cat {MODELS_DIR}/deployed.sha").strip()


def latest(conn):
    """The newest deployment row.

    Args:
        conn: A database connection. Rolled back first: these are session-scoped and a
            long-lived transaction would keep answering from the snapshot it opened with,
            which is exactly the row this is polling for a change to.

    Returns:
        The row as a dict, or an empty one before the first deployment.
    """
    conn.rollback()
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT sha, status, message, docs FROM crudman.system_deployment "
            "ORDER BY created_on DESC LIMIT 1"
        )
        row = cursor.fetchone()
    return dict(zip(("sha", "status", "message", "docs"), row)) if row else {}


def commits():
    """The branch's history as the versions page reads it, newest first."""
    output = crudman(
        f"cd {MODELS_DIR}/deployed && git log origin/main --format=%H%x1f%s"
    )
    return [
        {"sha": sha, "subject": subject}
        for sha, subject in (line.split("\x1f") for line in output.splitlines())
    ]


def push(subject, files):
    """Commit and push to the branch from a throwaway clone inside the container.

    Args:
        subject: The commit subject, which the versions page shows.
        files: Paths relative to the repository root, mapped to their contents.

    Returns:
        The sha of the commit that was pushed.
    """
    writes = "".join(
        f"mkdir -p $(dirname '{path}'); cat > '{path}' <<'CONTENT'\n{content}\nCONTENT\n"
        for path, content in files.items()
    )
    return crudman(
        "set -e; rm -rf /tmp/push; git clone -q \"$REPO_MODELS\" /tmp/push; cd /tmp/push; "
        f"{writes}"
        "git add --all; "
        "git -c user.name='Jean Dupont' -c user.email=jean@example.com "
        f"commit -q --message '{subject}'; "
        "git push -q origin main; git rev-parse HEAD"
    ).strip()


def wait_for(conn, sha, status):
    """Wait until a commit has been deployed and has reached a status.

    Returns:
        The deployment row.

    Raises:
        AssertionError: It did not happen within DEADLINE seconds.
    """
    deadline = time.time() + DEADLINE
    row = {}
    while time.time() < deadline:
        row = latest(conn)
        if row.get("sha") == sha and row.get("status") == status:
            return row
        time.sleep(3)
    raise AssertionError(f"{sha[:8]} never reached {status!r}: {row}")


def pin(sha):
    """Deploy one commit the way the versions page's button does."""
    crudman(
        "cd /crudman/app && uv run --project /crudman python manage.py shell -c "
        f"\"from system import repo; repo.deploy('{sha}', pinned=True)\""
    )


@pytest.fixture(scope="module")
def branch(admin_db):
    """Put the branch back where this module found it, so later modules see the models.

    Force-pushed, because a test may have added commits and the point is to leave the
    stack as it was rather than to preserve what the tests wrote.
    """
    original = crudman(f"cd {MODELS_DIR}/deployed && git rev-parse origin/main").strip()
    yield original
    crudman(
        "set -e; rm -rf /tmp/restore; git clone -q \"$REPO_MODELS\" /tmp/restore; "
        f"cd /tmp/restore; git reset -q --hard {original}; git push -q --force origin main"
    )
    wait_for(admin_db, original, "succeeded")


class TestTheShippedRepository:
    """What an installation given no git host of its own starts life with."""

    def test_the_repository_was_created_and_checked_out(self):
        assert sorted(crudman(f"ls {MODELS_DIR}").split()) == [
            "deployed", "deployed.sha", "origin.git", "workspaces",
        ]

    def test_the_engine_is_pointed_at_the_checked_out_commit(self):
        assert deployed_sha() == crudman(
            f"cd {MODELS_DIR}/deployed && git rev-parse HEAD"
        ).strip()

    def test_the_models_are_the_ones_this_release_ships(self):
        layers = crudman(f"ls {MODELS_DIR}/deployed/{PROJECT}/models").split()
        assert {"bronze", "silver", "gold"} <= set(layers)

    def test_there_is_a_history_to_move_through_on_the_first_day(self):
        # One commit per example tenant, so the versions page has something to show and
        # somewhere to go back to before anybody has pushed.
        subjects = [commit["subject"] for commit in reversed(commits())]

        assert len(subjects) == len(SEED_PROJECTS), subjects
        for subject, project in zip(subjects, SEED_PROJECTS):
            assert project in subject, subjects

    def test_a_clone_carries_what_a_developer_needs(self):
        # Cloning is meant to be the whole setup, so the connection settings travel with
        # the models rather than being written down somewhere a reader has to find.
        settings = crudman(f"cat {MODELS_DIR}/deployed/{PROJECT}/server.env")
        assert "SQLMESH_HOST=" in settings
        assert "SQLMESH_DATABASE=" in settings

    def test_the_container_side_tools_stayed_in_the_image(self):
        listed = crudman(f"ls {MODELS_DIR}/deployed/{PROJECT}").split()
        assert "Dockerfile" not in listed
        assert "entrypoint.sh" not in listed

    def test_the_engine_planned_it_and_described_what_it_built(self, admin_db):
        row = wait_for(admin_db, deployed_sha(), "succeeded")
        # The documentation pages read this, there being no export baked into an image.
        assert len(row["docs"]["layers"]) == 3
        # A plan that worked has nothing to say. Its log is thousands of lines of progress
        # written for a terminal, and the page shows whatever is stored.
        assert row["message"] == ""


class TestEveryShippedVersionPlans:
    """Each seeded commit is a working system, not just a point in a history.

    An earlier version has fewer tenants, and the harmonizing models are narrowed to
    match, so going back has to leave the engine planning rather than failing on a model
    that reads a tenant the commit does not have.
    """

    @pytest.mark.parametrize(
        "step, project", list(enumerate(SEED_PROJECTS)), ids=SEED_PROJECTS
    )
    def test_the_version_that_adds_a_tenant_plans(self, step, project, branch, admin_db):
        """Deploy the commit that added this tenant and let the engine plan it.

        One test per version rather than one loop over all of them, so a version that
        stops planning is named by the test that failed.
        """
        commit = list(reversed(commits()))[step]
        assert project in commit["subject"], commit

        pin(commit["sha"])
        row = wait_for(admin_db, commit["sha"], "succeeded")

        # What the engine parsed, which is the proof the narrowing was right: this version
        # has the tenants added up to here and none of the ones added after it.
        bronze = next(
            layer for layer in row["docs"]["layers"] if layer["name"] == "bronze"
        )
        described = " ".join(model["name"] for model in bronze["models"])
        for present in SEED_PROJECTS[: step + 1]:
            assert present in described, described
        for later in SEED_PROJECTS[step + 1 :]:
            assert later not in described, described


class TestDeployingAPush:
    """The point of the arrangement: a commit reaches production with nobody logged in."""

    def test_a_pushed_model_is_built(self, branch, admin_db, db):
        pushed = push(
            "Add a metric to the gold layer",
            {
                f"{PROJECT}/models/gold/deployment_probe.sql":
                    "MODEL (\n"
                    "  name gold.deployment_probe,\n"
                    "  kind FULL\n"
                    ");\n\n"
                    "SELECT 1 AS probe"
            },
        )

        wait_for(admin_db, pushed, "succeeded")

        db.rollback()
        with db.cursor() as cursor:
            cursor.execute("SELECT probe FROM gold.deployment_probe")
            assert cursor.fetchone()[0] == 1

    def test_the_documentation_follows_the_deployment(self, admin_session):
        page = admin_session.get(f"/{CRUDMAN_PATH}/docs/gold/").text
        assert "gold.deployment_probe" in page

    def test_the_versions_page_shows_the_commit(self, admin_session):
        page = admin_session.get(f"/{CRUDMAN_PATH}/system/deployment/").text
        assert "Add a metric to the gold layer" in page
        assert deployed_sha()[:8] in page


class TestARefusedCommit:
    """A commit the installed engine cannot run never reaches the working tree."""

    def test_a_dependency_change_is_refused_and_nothing_moves(self, branch, admin_db):
        running = deployed_sha()
        shipped = crudman(f"cat {MODELS_DIR}/deployed/{PROJECT}/pyproject.toml")

        pushed = push(
            "Add a dependency",
            {f"{PROJECT}/pyproject.toml": shipped + "\n# one more dependency\n"},
        )
        row = wait_for(admin_db, pushed, "failed")

        assert "pyproject.toml" in row["message"]
        # What protects the running system: the engine keeps the models it had.
        assert deployed_sha() == running

    def test_the_refusal_is_reported_rather_than_retried(self, branch, admin_db):
        before = latest(admin_db)
        time.sleep(15)
        assert latest(admin_db)["sha"] == before["sha"]


class TestAModelThatCannotBuild:
    """A plan that fails is reported, and reported so a person can read it.

    Not a refused deployment: a plan needs the database and can fail for reasons that have
    nothing to do with the commit, so the commit is checked out and the failure is news
    rather than a rejection.
    """

    def test_the_failure_is_recorded_against_the_commit(self, branch, admin_db):
        pushed = push(
            "Add a model that cannot build",
            {
                f"{PROJECT}/models/gold/broken.sql":
                    "MODEL (\n"
                    "  name gold.broken,\n"
                    "  kind FULL\n"
                    ");\n\n"
                    "SELECT * FROM silver.a_table_that_does_not_exist"
            },
        )

        row = wait_for(admin_db, pushed, "failed")

        assert row["message"], "a failed plan has to say why"
        # The log is written for a terminal and read in a browser, so the colour codes
        # would render as text in the middle of the sentence they were meant to colour.
        assert "\x1b[" not in row["message"]


class TestWhoMayReadAndDeploy:
    """Choosing what production computes is administration, not documentation."""

    def test_an_anonymous_visitor_is_sent_to_the_login_page(self, http):
        response = http.get(f"/{CRUDMAN_PATH}/system/deployment/")
        assert response.status_code == 302
        assert "login" in response.headers["location"]

    def test_the_documentation_does_not_link_to_it(self, admin_session):
        # It used to sit in the documentation sidebar, which is open from the viewer rank
        # up. Reading a metric must not come with the ability to change what produces it.
        page = admin_session.get(f"/{CRUDMAN_PATH}/docs/").text
        assert "Model versions" not in page

    def test_no_clone_address_is_offered_that_nobody_can_reach(self, admin_session):
        # The default repository lives on this volume alone, reachable from inside the
        # container and nowhere else. A clone command that cannot work is worse than none.
        page = admin_session.get(f"/{CRUDMAN_PATH}/system/deployment/").text
        assert "git clone" not in page
