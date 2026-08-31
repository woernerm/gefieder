"""Every builder passes the same build-time settings into the images.

Three scripts build the same five Dockerfiles: build.sh (docker, for the release
workflow), run-tests.sh (podman, this suite) and dev.sh (podman, local development), all
three sharing build_image in build-lib.sh. A setting they forget does not fail the build --
the Dockerfile's ARG default takes over -- so the image is built with a value the rest of
the system does not use. SERVER_STATS_SCHEMA is the sharp case: it decides which schema
gf_0007 creates, while the collector and the dashboards read the name from buildtime.env.

These read the scripts rather than the built images, because a mismatch only appears once
the setting is changed away from its default, and the default is what CI runs.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

BUILDERS_PODMAN = ["run-tests.sh", "dev.sh"]
BUILDER_DOCKER = "build.sh"

# The one group a podman builder need not pass: `podman build --http-proxy` defaults to
# true, so podman copies these from its own environment. docker does not, so build.sh must
# name them and is checked for them below.
PROXY_ARGS = {"http_proxy", "https_proxy", "no_proxy"}


def buildtime_settings():
    """The names of the settings in buildtime.env."""
    text = (REPO / "buildtime.env").read_text()
    return set(re.findall(r"^([A-Z_][A-Z0-9_]*)=", text, re.MULTILINE))


def declared_args():
    """Every ARG the Dockerfiles declare, including the FROM-level ones."""
    names = set()
    for dockerfile in REPO.glob("*/Dockerfile"):
        names |= set(re.findall(r"^ARG\s+([A-Za-z_][A-Za-z0-9_]*)",
                                dockerfile.read_text(), re.MULTILINE))
    return names


# The shared build library every builder sources: build_image and its --build-arg list
# live there, so a builder passes what it and this file name together.
BUILD_LIB = "build-lib.sh"


def passed_args(script):
    """The build-arg names a build script passes, the shared library included."""
    text = (REPO / script).read_text() + (REPO / BUILD_LIB).read_text()
    return set(re.findall(r'--build-arg\s+"?([A-Za-z_][A-Za-z0-9_]*)=', text))


# An ARG that also exists in buildtime.env is a setting the operator configures, so every
# builder must carry it into the image. One that does not is the Dockerfile's own business.
REQUIRED = declared_args() & buildtime_settings()


def test_the_required_build_args_shall_be_discovered():
    # A regex that stopped matching would leave every check below asserting over an empty
    # set, which passes and proves nothing.
    assert REQUIRED, "no configurable build args found -- the Dockerfile/env parsing broke"


def test_the_release_build_shall_pass_every_configurable_setting():
    missing = REQUIRED - passed_args(BUILDER_DOCKER)
    assert not missing, f"{BUILDER_DOCKER} does not pass: {sorted(missing)}"


def test_the_release_build_shall_pass_the_proxy_settings():
    # docker does not forward the host's proxy variables into a build.
    missing = PROXY_ARGS - passed_args(BUILDER_DOCKER)
    assert not missing, f"{BUILDER_DOCKER} does not pass: {sorted(missing)}"


def test_the_podman_builds_shall_pass_every_configurable_setting():
    for builder in BUILDERS_PODMAN:
        missing = REQUIRED - passed_args(builder)
        assert not missing, f"{builder} does not pass: {sorted(missing)}"
