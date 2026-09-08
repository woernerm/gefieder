"""What a person's notebook server starts life as: a Unix account and a working tree.

Each server runs as its own Unix user, so two people's homes, git credentials and the
database password in their environment are separated by the kernel rather than by
convention. The account is named exactly as crudman names their database role, which is
what lets ``sqlmesh/config.py`` derive the connection with no notebook-specific branch.

The working tree is a clone of the models repository under ``workspaces/`` on the models
volume, which ``crudman/app/system/requirements.md`` reserved for this: the deployed tree is
rewritten wholesale on every deployment, so nobody's uncommitted work may live there.
"""
import os
import pwd
import subprocess
from pathlib import Path

from jupyterhub.spawner import LocalProcessSpawner

MODELS_DIR = Path(os.environ.get("MODELS_DIR", "/var/lib/app/models"))
WORKSPACES = MODELS_DIR / "workspaces"
"""Where each person's clone lives, beside the deployed tree rather than inside it."""

ORIGIN = os.environ.get("REPO_MODELS", "").strip()
"""What a workspace is cloned from -- the same repository crudman deploys from, so a push
here is deployed by the ordinary poll and needs no path of its own."""

PROJECT = "sqlmesh"
"""The SQLMesh project inside the repository, which is what Lab opens on."""


class WorkspaceSpawner(LocalProcessSpawner):
    """A JupyterLab per person, on their own clone of the models repository."""

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

        WORKSPACES.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "clone", ORIGIN, str(workspace)],
            check=True,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        # The hub clones as root, so the tree is handed to the person who works in it.
        # Without this the git panel cannot write a single file.
        for path in [workspace, *workspace.rglob("*")]:
            os.chown(path, account.pw_uid, account.pw_gid)
        return workspace

    @staticmethod
    def _refresh_skeleton(account) -> None:
        """Copy the shipped IPython configuration into an existing home.

        ``/etc/skel`` is copied only when an account is created, so a person who signed in
        under an earlier release keeps whatever it held then -- and the kernel would go on
        starting without SQLMesh loaded. Overwritten rather than merged: it is this
        system's file, and a person who wants their own additions has the profile's
        startup directory beside it.

        Args:
            account: The Unix account record the server runs as.
        """
        source = Path("/etc/skel/.ipython/profile_default/ipython_kernel_config.py")
        if not source.exists():
            return

        target = Path(
            account.pw_dir, ".ipython", "profile_default", "ipython_kernel_config.py"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text())
        for path in (target, target.parent, target.parent.parent):
            os.chown(path, account.pw_uid, account.pw_gid)

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
        return super().start()
