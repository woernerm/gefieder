"""What a person's notebook server starts life as: a Unix account and a working tree.

Each server runs as its own Unix user, so homes, git credentials and database passwords
are separated by the kernel. The account is named as crudman names the database role, so
``sqlmesh/config.py`` needs no notebook-specific branch.

The working tree is a clone of the models repository under ``workspaces/``, as the
deployed tree is rewritten on every deployment.
"""
import os
import pwd
import subprocess
from pathlib import Path

from jupyterhub.spawner import LocalProcessSpawner

MODELS_DIR = Path(os.environ.get("MODELS_DIR", "/var/lib/app/models"))
WORKSPACES = MODELS_DIR / "workspaces"
"""Where each person's clone is kept, beside the deployed tree rather than inside it."""

ORIGIN = os.environ.get("REPO_MODELS", "").strip()
"""What a workspace is cloned from -- the same repository crudman deploys from, so a push
here is deployed by the ordinary poll and needs no path of its own."""

PROJECT = "sqlmesh"
"""The SQLMesh project inside the repository, which is what Lab opens on."""


class WorkspaceSpawner(LocalProcessSpawner):
    """A JupyterLab per person, on their own clone of the models repository."""

    SKELETON_FILES = (
        ".ipython/profile_default/ipython_kernel_config.py",
        ".jupyter/custom/custom.css",
        ".jupyter/custom/palette.css",
    )
    """What every home holds, whichever release the account was created under. The first
    loads SQLMesh into the kernel; the other two are the system's colours and the
    stylesheet that applies them to Lab, which imports the palette from beside itself."""

    INHERITED = ("PATH", "http_proxy", "https_proxy", "no_proxy", "SSL_CERT_FILE")
    """What a command run as the person sees of the hub's environment, upper case included.
    Nothing more, as they can read a process of their own, and the hub holds secrets."""

    @classmethod
    def _as_person(cls, account, command, **environment) -> None:
        """Run a command with a person's own rights.

        Never as the hub, which is root: what a person owns may hold a link to anywhere, and
        root would write through it.

        Args:
            account: The Unix account record to run as.
            command: The command and its arguments.
            **environment: Variables to set beside the inherited ones.
        """
        names = {variant for name in cls.INHERITED for variant in (name, name.upper())}
        subprocess.run(
            command,
            check=True,
            cwd="/",
            user=account.pw_uid,
            group=account.pw_gid,
            extra_groups=[],
            umask=0o022,
            env={name: os.environ[name] for name in names if name in os.environ}
            | {"HOME": account.pw_dir}
            | environment,
        )

    def _clone(self, account) -> Path:
        """Make sure this person has a working tree, and return it.

        Cloned once and then left alone: it holds their branches and their uncommitted
        edits, so a later spawn must not disturb it. Pulling is theirs to do in the git
        panel, as it would be on a laptop.

        Args:
            account: The Unix account record the server runs as.

        Returns:
            The path of the workspace.
        """
        workspace = WORKSPACES / self.user.name
        if (workspace / ".git").exists():
            return workspace

        workspace.mkdir(parents=True, exist_ok=True)
        os.chown(workspace, account.pw_uid, account.pw_gid)
        self._as_person(
            account, ["git", "clone", ORIGIN, str(workspace)], GIT_TERMINAL_PROMPT="0"
        )
        return workspace

    @staticmethod
    def _refresh_skeleton(account) -> None:
        """Copy the shipped configuration into an existing home.

        ``/etc/skel`` is copied only when an account is created, so a home from an earlier
        release would start the kernel without SQLMesh. Overwritten, as these are this system's
        files; a person's own additions belong in the profile's startup directory.

        Args:
            account: The Unix account record the server runs as.
        """
        for relative in WorkspaceSpawner.SKELETON_FILES:
            source = Path("/etc/skel", relative)
            if source.exists():
                target = Path(account.pw_dir, relative)
                WorkspaceSpawner._as_person(
                    account, ["install", "-D", "-T", "-m", "644", str(source), str(target)]
                )

    def start(self):
        """Prepare the workspace, then start the server in it.

        The Unix account itself is the authenticator's to create (``add_user``), which is
        why this can rely on it being there.
        """
        account = pwd.getpwnam(self.user.name)
        self._refresh_skeleton(account)
        workspace = self._clone(account)
        # The repository root, not the SQLMesh project inside it: the git panel looks for
        # .git at or below the server's root directory, and rooting one level down leaves
        # it reporting no repository at all. Lab still opens on the project, through
        # default_url below, so the file browser lands where the models are.
        self.notebook_dir = str(workspace)
        self.default_url = f"/lab/tree/{PROJECT}"
        # What the terminal treats as its top folder (bash_prompt.sh). A fresh dict, as
        # crudman.py explains for its own entries.
        self.environment = {**self.environment, "TERMINAL_ROOT": str(workspace / PROJECT)}
        return super().start()
