"""Secrets hygiene CLAUDE.md implies: passwords and keys are podman secrets, so their
values never appear in the rendered quadlet unit files or baked into the images.

The quadlets reference secrets by name (Secret=...) and the images receive them at
runtime via /run/secrets, so neither the unit files nor the image config/history should
contain any secret value.
"""
import json
import os

import pytest

from conftest import CONTAINERS, SUPERUSER_NAME, podman

# The actual secret values, from the same env the suite uses for its DB connections.
SECRET_VALUES = {
    "superuser_password": os.environ["TEST_SUPERUSER_PASSWORD"],
    "crudman_password": os.environ["TEST_CRUDMAN_PASSWORD"],
    "sqlmesh_password": os.environ["TEST_SQLMESH_PASSWORD"],
    "grafana_password": os.environ["TEST_GRAFANA_PASSWORD"],
}

# Config values the quadlets are meant to contain in plain text. The dev profile sets the
# superuser password equal to the (public) superuser name "admin", so that value appears
# legitimately wherever the name is rendered. A secret whose value equals one of these is
# indistinguishable from the legitimate token by a substring scan, so it is excluded from
# the leak check below; the production profile uses a random password and is unaffected.
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

    These are where a baked-in secret would surface: an ENV line, or a RUN/COPY that
    embedded the value. Read from the image (not the container) so a runtime-only
    /run/secrets mount is not mistaken for a baked-in value.
    """
    image = json.loads(podman("inspect", container))[0]["ImageName"]
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

    @pytest.mark.parametrize("name", list(SECRET_VALUES) + ["django_secret_key"])
    def test_secrets_shall_exist_as_podman_secrets(self, name):
        # The credentials are managed as podman secrets, not inline config.
        names = podman("secret", "ls", "--format", "{{.Name}}").split()
        assert name in names, f"{name} is not a podman secret"
