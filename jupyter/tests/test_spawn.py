"""What the hub does in a person's home, checked against a home set up to exploit it.

The hub is root and a person may put a link to anywhere in what they own.
"""
import os
import pwd
import subprocess
import sys

import pytest

sys.path.insert(0, "/etc/jupyterhub")

import spawn


@pytest.fixture
def account():
    subprocess.run(["useradd", "--create-home", "jdoe"], check=True)
    yield pwd.getpwnam("jdoe")
    subprocess.run(["userdel", "--remove", "jdoe"], check=True)


@pytest.fixture
def victim(tmp_path):
    path = tmp_path / "victim"
    path.write_text("root's own")
    return path


def test_a_linked_file_is_replaced_rather_than_written_through(account, victim):
    target = f"{account.pw_dir}/.jupyter/custom/custom.css"
    os.unlink(target)
    os.symlink(victim, target)

    spawn.WorkspaceSpawner._refresh_skeleton(account)

    assert victim.read_text() == "root's own"
    assert victim.stat().st_uid == 0
    assert not os.path.islink(target)
    assert os.stat(target).st_uid == account.pw_uid


def test_a_linked_directory_is_not_written_into(account, tmp_path):
    subprocess.run(["rm", "-r", f"{account.pw_dir}/.jupyter/custom"], check=True)
    os.symlink(tmp_path, f"{account.pw_dir}/.jupyter/custom")

    with pytest.raises(subprocess.CalledProcessError):
        spawn.WorkspaceSpawner._refresh_skeleton(account)

    assert list(tmp_path.iterdir()) == []


def test_a_command_run_as_the_person_sees_no_secret_of_the_hub(account, monkeypatch, capfd):
    monkeypatch.setenv("JUPYTERHUB_CRYPT_KEY", "secret")

    spawn.WorkspaceSpawner._as_person(account, ["env"])

    assert "JUPYTERHUB_CRYPT_KEY" not in capfd.readouterr().out
