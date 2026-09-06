"""The models repository: where it comes from, what is deployed, and how that changes.

Every operation here is a git command. Nothing reimplements a repository format, reads an
object file or parses a ref, because git is already installed and already correct.

crudman is the only writer. It clones ``REPO_MODELS`` into ``deployed/`` and checks a
commit out there; the engine mounts the same volume read-only and runs whatever it finds.
So a model reaches production as a commit rather than as a release.
"""

import os
import shutil
import subprocess
from pathlib import Path

from django.conf import settings
from django.db import connection, transaction

MODELS_DIR = Path(os.environ.get("MODELS_DIR", "/var/lib/app/models"))
"""The volume crudman writes and the engine reads, at the same path in both."""

ORIGIN = os.environ.get("REPO_MODELS", "").strip()
"""Where the models come from (REPO_MODELS in buildtime.env).

A path is a repository on this volume, created and seeded on first start. Anything else is
handed to ``git clone`` unchanged, and is then the origin that owns the branch.
"""

DEPLOYED = MODELS_DIR / "deployed"
"""The working tree the engine runs. Written by nothing but ``checkout`` below."""

MARKER = MODELS_DIR / "deployed.sha"
"""The commit in the working tree, written once that tree is complete.

The engine watches this one file rather than the tree, which makes it both the answer to
"what is deployed" and the signal that it is safe to read: a checkout in progress has not
written it yet. Reading it needs no git, so the engine image carries none.
"""

WORKSPACES = MODELS_DIR / "workspaces"
"""Reserved for working trees people edit in, kept apart from the deployed one.

Deploying a version rewrites ``deployed/`` wholesale. Nobody's uncommitted work may live
there, so anything that lets a person edit models on this server gets a tree of its own
here and reaches production the same way everything else does: by pushing a commit.
"""

SEED = Path(os.environ.get("MODELS_SEED", "/seed"))
"""What a repository this system creates starts life as, baked into the crudman image."""

SEED_PROJECTS = ("project_a", "project_b", "project_c")
"""The example tenants the seed ships, in the order the first commits add them.

The seed becomes one commit per tenant rather than one commit for everything, so a fresh
installation has versions to move between and the page has something to demonstrate. Each
adds a tenant to a system that already computes, which is also the change a real one makes
most often.

Named here rather than discovered, because a file belongs to a tenant by having the name
in its path and nothing else says so. Editing the shipped examples means editing this line.
"""

PROJECT = "sqlmesh"
"""The SQLMesh project inside the repository.

A subdirectory rather than the repository root, so that what belongs beside the models --
dashboards, notebooks, whatever a later version keeps with them -- has somewhere to go
without moving anything.
"""

BRANCH = "main"
"""The one branch that deploys. Others are for collaboration and are never checked out."""

LOCK_KEY = 0x6D6F64656C73
"""Advisory lock guarding the working tree, so two deployments cannot interleave.

A PostgreSQL lock rather than a file on the volume: it is held by a transaction, so it is
released even when the process holding it dies, which a lock file is not.
"""


class GitError(RuntimeError):
    """A git command failed. Carries what git wrote, which is what a reader needs."""


def git(*args: str, cwd: Path | None = None) -> str:
    """Run one git command and return its output.

    Args:
        *args: The command and its arguments, without the leading "git".
        cwd: Where to run it; the deployed working tree by default.

    Returns:
        Standard output, stripped.

    Raises:
        GitError: git exited non-zero, with its stderr as the message.
    """
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd or DEPLOYED),
        capture_output=True,
        text=True,
        # A prompt would hang the request forever; failing is the only useful answer.
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    if result.returncode:
        raise GitError((result.stderr or result.stdout).strip())
    return result.stdout.strip()


def is_local() -> bool:
    """Whether the origin is the repository this system hosts on its own volume."""
    return ORIGIN.startswith("/")


def clone_url() -> str | None:
    """The address a developer clones, or None when there is nothing they can reach.

    The local default is reachable only from inside the container, so rather than print an
    address that does not work, the page says so and names the setting that changes it.
    """
    return None if is_local() else ORIGIN


def _narrow_unions(work: Path, projects: list[str]) -> None:
    """Leave the harmonizing models unioning only the tenants that are present.

    A silver model directly under ``models/silver/`` stacks one branch per tenant, and a
    branch reading a tenant that has not been added yet would fail to parse. So the earlier
    commits get the same file with the later branches taken out, which is exactly the diff
    adding a tenant produces in the other direction.

    Rewritten rather than shipped as variants: the branches are generated from the file the
    project actually runs, so editing that file cannot leave a stale copy behind.

    Args:
        work: The working tree being built.
        projects: The tenants this commit has.
    """
    absent = [project for project in SEED_PROJECTS if project not in projects]
    if not absent:
        return

    for model in (work / PROJECT / "models" / "silver").glob("*.sql"):
        text = model.read_text()
        if "UNION ALL" not in text:
            continue

        # The MODEL block ends at the first ");", and the query is what follows.
        head, end, query = text.partition(");")
        kept = [
            branch
            for branch in query.split("UNION ALL")
            if not any(project in branch for project in absent)
        ]
        model.write_text(head + end + "UNION ALL".join(kept))


def _write_seed(work: Path, projects: list[str]) -> None:
    """Fill the working tree with the seed, holding only the given example tenants.

    Args:
        work: The working tree, whose .git directory is left alone.
        projects: The tenants this commit has.
    """
    for entry in work.iterdir():
        if entry.name == ".git":
            continue
        shutil.rmtree(entry) if entry.is_dir() else entry.unlink()

    absent = [project for project in SEED_PROJECTS if project not in projects]
    for source in SEED.rglob("*"):
        relative = source.relative_to(SEED)
        if not source.is_file() or any(project in str(relative) for project in absent):
            continue
        target = work / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    (work / PROJECT).mkdir(parents=True, exist_ok=True)
    (work / PROJECT / "server.env").write_text(_server_env())
    _narrow_unions(work, projects)


def _seed() -> None:
    """Create the origin repository from the project this release ships.

    Only when REPO_MODELS names a path that does not exist. Shipping the examples is what
    keeps a fresh installation runnable end to end, and shipping them as one commit per
    tenant is what gives the versions page something to move between on the first day.

    Every commit is a project that plans: the harmonizing models are narrowed to the
    tenants each one has, so an earlier version is a smaller working system rather than a
    broken one.
    """
    origin = Path(ORIGIN)
    git("init", "--bare", f"--initial-branch={BRANCH}", str(origin), cwd=MODELS_DIR)

    work = MODELS_DIR / ".seed"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    git("init", f"--initial-branch={BRANCH}", cwd=work)

    present = [
        project for project in SEED_PROJECTS if any(SEED.rglob(f"*{project}*"))
    ]
    for step in range(1, len(present) + 1):
        projects = present[:step]
        _write_seed(work, projects)
        git("add", "--all", cwd=work)
        git(
            "-c", f"user.name={settings.APP_NAME}",
            "-c", "user.email=noreply@localhost",
            "commit", "--message", f"Add the example tenant {projects[-1]}",
            cwd=work,
        )

    # A seed carrying no example tenant is still a repository, and still needs its commit.
    if not present:
        _write_seed(work, [])
        git("add", "--all", cwd=work)
        git(
            "-c", f"user.name={settings.APP_NAME}",
            "-c", "user.email=noreply@localhost",
            "commit", "--message", f"The analytics models {settings.APP_NAME} ships",
            cwd=work,
        )

    git("push", str(origin), BRANCH, cwd=work)
    shutil.rmtree(work, ignore_errors=True)


def _server_env() -> str:
    """What a clone needs to reach this system's database, as sqlmesh/config.py reads it.

    Committed rather than documented, so cloning the repository is the whole setup: a
    developer adds their password and nothing else. It holds no secret -- an address, a
    port, a database name and the prefix their role carries.
    """
    return (
        "# Written when this repository was created, so a clone is developable on its own.\n"
        "# sqlmesh/config.py reads it; an environment variable of the same name wins, and\n"
        "# your password belongs in sqlmesh/.env, which is never committed.\n"
        f"SQLMESH_HOST={settings.SERVER_NAME}\n"
        f"SQLMESH_PORT={os.environ.get('PG_PORT', '5432')}\n"
        f"SQLMESH_DATABASE={os.environ.get('POSTGRES_DB', 'postgres')}\n"
        f"SQLMESH_USER_PREFIX={os.environ.get('DB_USER_PREFIX', '')}\n"
    )


def ensure() -> None:
    """Make sure the origin and the working tree exist. Safe to call on every start.

    Raises:
        GitError: REPO_MODELS is empty, which leaves nothing to clone and no history to
            keep. The local default exists so that having an origin costs nothing.
    """
    if not ORIGIN:
        raise GitError(
            "REPO_MODELS is empty. Set it in buildtime.env, either to a git repository "
            "or to a path on the models volume for this system to host itself."
        )

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    WORKSPACES.mkdir(exist_ok=True)

    if is_local() and not Path(ORIGIN).exists():
        # A working tree without the repository it came from is damage, not a fresh
        # install, and seeding would answer it by replacing someone's history with the
        # examples this release ships. So it stops instead and says what it found.
        if (DEPLOYED / ".git").exists():
            raise GitError(
                f"{ORIGIN} is missing although a working tree is still there. Creating it "
                "again would replace the history with the models this release ships, so "
                "nothing was changed. Restore the volume from a backup, or remove "
                f"{DEPLOYED} to start over."
            )
        _seed()

    if not (DEPLOYED / ".git").exists():
        shutil.rmtree(DEPLOYED, ignore_errors=True)
        git("clone", "--branch", BRANCH, ORIGIN, str(DEPLOYED), cwd=MODELS_DIR)


def fetch() -> None:
    """Bring the origin's branches up to date. A no-op when nobody has pushed."""
    git("fetch", "--quiet", "--prune", "origin")


def head_of_main() -> str:
    """The commit the origin's branch points at."""
    return git("rev-parse", f"origin/{BRANCH}")


def deployed_sha() -> str | None:
    """The commit currently checked out, or None before the first clone."""
    try:
        return git("rev-parse", "HEAD")
    except GitError:
        return None


def log(limit: int = 50) -> list[dict]:
    """The branch's history, newest first.

    The deployed commit is named alongside the branch, so it is listed even when the branch
    no longer reaches it. A force-push or a rebase on the git host is enough to strand it,
    and a list of versions that omits the running one is the one thing this page must never
    do. git deduplicates it when it is on the branch, which is the ordinary case.

    Args:
        limit: How many commits to read.

    Returns:
        One dict per commit with its sha, author, ISO-8601 date and subject. Empty before
        the first clone, so the page renders rather than failing.
    """
    revisions = [f"origin/{BRANCH}"]
    if deployed := deployed_sha():
        revisions.append(deployed)

    try:
        output = git(
            "log", *revisions, f"--max-count={limit}",
            # Unit separator: a subject may hold anything a person can type, but not this.
            "--format=%H%x1f%an%x1f%aI%x1f%s",
        )
    except GitError:
        return []

    commits = []
    for line in output.splitlines():
        sha, author, date, subject = line.split("\x1f")
        commits.append(
            {"sha": sha, "short_sha": sha[:8], "author": author,
             "date": date, "subject": subject}
        )
    return commits


def dependency_mismatch(sha: str) -> str | None:
    """Why this commit cannot run on the engine that is installed, if it cannot.

    The engine image resolves its dependencies at build time from the project's
    pyproject.toml, so a commit that changes that file asks for an environment this
    release does not have. Checking it out would leave the engine failing on an import
    with no way back, so the deployment is refused before anything moves.

    Compared against the seed, which is this release's copy of the same file: the two come
    from one build, so they agree by construction.

    Args:
        sha: The commit to check.

    Returns:
        The reason, in the words the versions page shows, or None when it can run.
    """
    shipped = SEED / PROJECT / "pyproject.toml"
    if not shipped.exists():
        return None

    try:
        wanted = git("show", f"{sha}:{PROJECT}/pyproject.toml")
    except GitError:
        return f"This commit has no {PROJECT}/pyproject.toml, so it is not a models repository."

    if wanted.strip() != shipped.read_text().strip():
        return (
            f"This commit changes {PROJECT}/pyproject.toml. Dependencies are installed "
            "when the images are built, not when a commit is deployed, so it needs a new "
            "release of the system before it can run. Everything else deploys as usual."
        )
    return None


def is_on_branch(sha: str) -> bool:
    """Whether a commit is one the branch actually reaches.

    What bounds the versions page: only history that was pushed may be deployed, so a
    reference typed into the form cannot reach a commit nobody has seen.
    """
    try:
        git("merge-base", "--is-ancestor", sha, f"origin/{BRANCH}")
        return True
    except GitError:
        return False


def checkout(sha: str) -> None:
    """Put one commit into the deployed working tree.

    Detached on purpose: the tree is a rendering of a commit, never a branch someone could
    commit onto by mistake.
    """
    git("checkout", "--force", "--detach", sha)
    git("clean", "--force", "-d")
    MARKER.write_text(f"{sha}\n")


def deploy(sha, *, pinned=False, user=None, main_sha=None):
    """Deploy one commit and record the attempt.

    The engine notices the tree has changed on its next pass and plans it, which is what
    turns the row from applying to live.

    Args:
        sha: The commit to deploy.
        pinned: Whether a person chose it rather than the poll.
        user: Who asked, or None for the poll.
        main_sha: Where the branch stood, so the poll knows it has already seen this.

    Returns:
        The Deployment row, already failed when the commit was refused.
    """
    from .models import Deployment

    with transaction.atomic():
        # Serializes with any other deployment, and is released by the end of this
        # transaction however it ends.
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [LOCK_KEY])

        record = {
            "sha": sha,
            "main_sha": main_sha or head_of_main(),
            "pinned": pinned,
            "requested_by": user,
        }

        # Recorded as seen even though it was refused, so the poll reports it once rather
        # than retrying the same commit every interval.
        if reason := dependency_mismatch(sha):
            return Deployment.objects.create(
                status=Deployment.FAILED, message=reason, **record
            )

        checkout(sha)
        return Deployment.objects.create(**record)


def poll(user=None):
    """Deploy what the branch points at, unless that has already been seen.

    A commit someone pinned therefore stands until the branch moves, and the next push
    supersedes it without any pin to clear.

    Returns:
        The Deployment row, or None when there was nothing to do.
    """
    from .models import Deployment

    ensure()
    fetch()
    head = head_of_main()

    latest = Deployment.objects.first()
    if latest and latest.main_sha == head:
        return None

    return deploy(head, main_sha=head, user=user)
