"""The models repository over SSH: cloned and pushed from outside, as from a laptop.

The key is registered for the superuser directly in the database and git runs on the host
against the published SFTP port, so the whole path is exercised: the key sign-in, the
rank check, the git commands and the volume behind them. Pushes go to a branch of the
suite's own, which nothing deploys, and refusals are asserted on that branch too.
"""
import os
import shutil
import subprocess

import pytest

from conftest import SFTP_PORT, SUPERUSER_NAME, crudman, django

BRANCH = "ssh-suite"
URL = f"ssh://git@localhost:{SFTP_PORT}/models.git"

pytestmark = pytest.mark.skipif(
    not (shutil.which("git") and shutil.which("ssh") and shutil.which("ssh-keygen")),
    reason="needs git and OpenSSH on the host",
)


def keypair(directory, name):
    """A fresh ed25519 key pair; returns the private key's path and the public line."""
    path = directory / name
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "ssh-suite", "-f", str(path)],
        check=True,
    )
    return path, (directory / f"{name}.pub").read_text().strip()


def git(key, *args, cwd=None, check=True):
    """Run git with this key alone, trusting the throwaway stack's host key."""
    command = (
        f"ssh -i {key} -o IdentitiesOnly=yes -o StrictHostKeyChecking=no "
        "-o UserKnownHostsFile=/dev/null -o LogLevel=ERROR"
    )
    return subprocess.run(
        ["git", "-c", "user.name=Jean Dupont", "-c", "user.email=jean@example.com", *args],
        cwd=cwd,
        env={**os.environ, "GIT_SSH_COMMAND": command, "GIT_TERMINAL_PROMPT": "0"},
        capture_output=True,
        text=True,
        check=check,
    )


@pytest.fixture(scope="module")
def key(tmp_path_factory):
    """A key registered for the superuser, removed again with the suite's branch."""
    path, public = keypair(tmp_path_factory.mktemp("keys"), "admitted")
    django(
        "from django.contrib.auth.models import User; from system import ssh; "
        "from system.models import SSHKey; "
        f"line, fingerprint, _ = ssh.parse_key('{public}'); "
        f"SSHKey.objects.create(user=User.objects.get(username='{SUPERUSER_NAME}'), "
        "title='ssh-suite', public_key=line, fingerprint=fingerprint)"
    )
    yield path
    django("from system.models import SSHKey; SSHKey.objects.filter(title='ssh-suite').delete()")
    crudman(f'git --git-dir="$REPO_MODELS" branch -D {BRANCH} 2>/dev/null || true')


@pytest.fixture(scope="module")
def clone(key, tmp_path_factory):
    """A clone made over SSH, as the Versions page tells a developer to."""
    work = tmp_path_factory.mktemp("clone") / "models"
    git(key, "clone", "-q", URL, str(work))
    return work


def origin_sha(ref):
    return crudman(f'git --git-dir="$REPO_MODELS" rev-parse {ref}').strip()


class TestClone:
    def test_the_clone_is_the_branch_that_deploys(self, key, clone):
        head = git(key, "rev-parse", "HEAD", cwd=clone).stdout.strip()
        assert head == origin_sha("main")

    def test_an_unknown_key_is_refused(self, tmp_path):
        stranger, _ = keypair(tmp_path, "stranger")
        result = git(stranger, "clone", "-q", URL, str(tmp_path / "x"), check=False)
        assert result.returncode != 0
        assert "Permission denied" in result.stderr

    def test_a_shell_is_refused(self, key):
        result = subprocess.run(
            ["ssh", "-i", str(key), "-o", "IdentitiesOnly=yes",
             "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
             "-o", "LogLevel=ERROR", "-p", str(SFTP_PORT), "git@localhost", "ls /"],
            capture_output=True, text=True,
        )
        assert result.returncode != 0
        assert "serves git alone" in result.stderr


class TestPush:
    def test_a_push_reaches_the_origin(self, key, clone):
        (clone / "ssh-suite.txt").write_text("pushed over SSH\n")
        git(key, "checkout", "-q", "-b", BRANCH, cwd=clone)
        git(key, "add", "ssh-suite.txt", cwd=clone)
        git(key, "commit", "-q", "-m", "Pushed over SSH", cwd=clone)
        git(key, "push", "-q", "origin", BRANCH, cwd=clone)

        head = git(key, "rev-parse", "HEAD", cwd=clone).stdout.strip()
        assert origin_sha(BRANCH) == head

    def test_history_is_not_rewritten(self, key, clone):
        pushed = origin_sha(BRANCH)
        git(key, "commit", "-q", "--amend", "-m", "Rewritten", cwd=clone)

        result = git(key, "push", "-q", "--force", "origin", BRANCH, cwd=clone, check=False)

        assert result.returncode != 0
        assert origin_sha(BRANCH) == pushed

    def test_a_branch_is_not_deleted(self, key, clone):
        result = git(key, "push", "-q", "origin", f":{BRANCH}", cwd=clone, check=False)

        assert result.returncode != 0
        assert origin_sha(BRANCH)
