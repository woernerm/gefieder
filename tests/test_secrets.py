"""Passwords and keys are podman secrets, never values in a unit file or an image.

The quadlets reference them by name (Secret=...) and the images receive them at runtime
under /run/secrets, so neither the units nor the image config and history may carry a
secret value.
"""
import json
import os

import pytest

from conftest import CONTAINERS, SECRETS, SUPERUSER_NAME, inspect_container, podman

# The values themselves, from the env the suite uses for its database connections.
SECRET_VALUES = {
    SECRETS["superuser"]: os.environ["TEST_SUPERUSER_PASSWORD"],
    SECRETS["crudman"]: os.environ["TEST_CRUDMAN_PASSWORD"],
    SECRETS["sqlmesh"]: os.environ["TEST_SQLMESH_PASSWORD"],
    SECRETS["grafana"]: os.environ["TEST_GRAFANA_PASSWORD"],
}

# Config values the quadlets legitimately contain in plain text. The dev profile sets the
# superuser password equal to the public superuser name, and a substring scan cannot tell
# the two apart, so such a value is excluded below. Production uses a random password.
PUBLIC_TOKENS = {SUPERUSER_NAME}


def _leakable(values):
    """The secret values worth scanning for: those not equal to a public config token."""
    return {name: value for name, value in values.items() if value not in PUBLIC_TOKENS}

QUADLET_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")),
    "containers", "systemd",
)


def _image_text(container):
    """All env values and layer-creating commands of a container's image, as one string.

    Where a baked-in secret would surface: an ENV line, or a RUN/COPY that embedded the
    value. Read from the image, so a runtime-only /run/secrets mount is not mistaken for
    one.
    """
    image = inspect_container(container)["ImageName"]
    cfg = json.loads(podman("image", "inspect", image))[0]
    env = cfg.get("Config", {}).get("Env", []) or []
    history = [h.get("created_by", "") for h in cfg.get("History", [])]
    return "\n".join(env + history)


class TestSecretsNotInImages:
    """No secret value is baked into an image."""

    @pytest.mark.parametrize("container", CONTAINERS)
    def test_no_secret_value_shall_appear_in_an_image(self, container):
        text = _image_text(container)
        for name, value in _leakable(SECRET_VALUES).items():
            assert value not in text, f"{name} value is baked into the {container} image"


class TestSecretsNotInQuadlets:
    """The rendered unit files reference secrets by name, never by value."""

    def test_no_secret_value_shall_appear_in_a_quadlet(self):
        blob = ""
        for fname in os.listdir(QUADLET_DIR):
            with open(os.path.join(QUADLET_DIR, fname), encoding="utf-8") as fh:
                blob += fh.read()
        for name, value in _leakable(SECRET_VALUES).items():
            assert value not in blob, f"{name} value is present in a rendered quadlet"

    # oidc_client_secret holds only the installer's placeholder until an operator sets the
    # real one, but it must exist: the containers referencing it will not start otherwise.
    @pytest.mark.parametrize(
        "name", list(SECRET_VALUES) + [SECRETS["django_key"], SECRETS["oidc_client"]]
    )
    def test_secrets_shall_exist_as_podman_secrets(self, name):
        # Credentials are podman secrets, not inline config.
        names = podman("secret", "ls", "--format", "{{.Name}}").split()
        assert name in names, f"{name} is not a podman secret"
