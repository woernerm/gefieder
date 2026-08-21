"""The published ports come from runtime.env, and every link in that chain holds.

quadlets/main.pod names its host ports as ${TOKEN}s. Nothing expands them at build time on
purpose: envsubst leaves a token its allowlist does not carry, quadlet copies the line into
the generated unit verbatim, and systemd expands it from the EnvironmentFile when the pod
starts. Three things can break that silently, and none of them fails the build:

  * a token runtime.env does not declare expands to nothing, so podman is handed
    "--publish :5432" and the pod fails to start -- on the target machine, at first boot;
  * a token added to an envsubst allowlist is frozen into the shipped unit instead, and the
    operator's runtime.env is then ignored with no sign of it;
  * a pod file without an EnvironmentFile leaves systemd nothing to expand from at all.

That the ports then actually work is covered by the rest of the suite, which reaches the
database, the SFTP endpoint and the Flight endpoint on the isolated ports run-tests.sh puts
in the test runtime.env -- so these are the static checks that a *changed* port would not
be quietly ignored.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

POD = (REPO / "quadlets" / "main.pod").read_text()

# The envsubst allowlists that render the quadlets, in the two places that carry a copy.
ALLOWLIST_SCRIPTS = ["run-tests.sh", ".github/workflows/publish.yml"]


def pod_port_tokens():
    """The ${TOKEN}s the pod's PublishPort lines expand at start."""
    lines = [l for l in POD.splitlines() if l.startswith("PublishPort=")]
    assert lines, "main.pod publishes no ports -- the parsing broke"
    return set(re.findall(r"\$\{([A-Z_][A-Z0-9_]*)\}", "\n".join(lines)))


def runtime_settings():
    """The names declared in runtime.env."""
    text = (REPO / "runtime.env").read_text()
    return set(re.findall(r"^([A-Z_][A-Z0-9_]*)=", text, re.MULTILINE))


def allowlisted(script):
    """The names in a VARS='${A} ${B}' envsubst allowlist."""
    text = (REPO / script).read_text()
    line = re.search(r"^\s*VARS='([^']*)'", text, re.MULTILINE)
    assert line, f"{script} has no VARS allowlist"
    return set(re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", line.group(1)))


def test_the_pod_shall_publish_its_ports_by_variable():
    # A literal port here is the old arrangement, where moving one meant editing the
    # installed unit file by hand.
    assert pod_port_tokens(), "main.pod publishes literal ports, not runtime.env settings"


def test_every_published_port_shall_be_declared_in_runtime_env():
    missing = pod_port_tokens() - runtime_settings()
    assert not missing, f"main.pod expands settings runtime.env does not declare: {sorted(missing)}"


def test_no_published_port_shall_be_substituted_at_build_time():
    for script in ALLOWLIST_SCRIPTS:
        frozen = pod_port_tokens() & allowlisted(script)
        assert not frozen, f"{script} bakes the ports into the shipped unit: {sorted(frozen)}"


def test_the_pod_shall_read_the_runtime_configuration():
    # systemd expands ExecStartPre against the unit's own environment, which this is the
    # only source of: the pod runs no container, so it passes nothing to --env-file.
    assert re.search(r"^EnvironmentFile=", POD, re.MULTILINE), \
        "main.pod has no EnvironmentFile, so its ${...} ports expand to nothing"


def test_the_installer_shall_preflight_the_configured_ports():
    # The installer sources runtime.env before this list, so it checks what will be bound
    # rather than the defaults it used to name literally.
    text = (REPO / "install.sh").read_text()
    ports = re.search(r'^PORTS="([^"]*)"', text, re.MULTILINE)
    assert ports, "install.sh has no PORTS preflight list"
    checked = set(re.findall(r"\$\{([A-Z_][A-Z0-9_]*)\}", ports.group(1)))
    assert checked == pod_port_tokens(), (
        f"install.sh checks {sorted(checked)}, the pod publishes {sorted(pod_port_tokens())}"
    )


def test_the_dropzone_healthchecks_shall_follow_their_port():
    # These two publish the port they listen on, so a moved port has to reach the probe as
    # well -- a literal one leaves the unit failing to start on a changed runtime.env.
    for svc, var in (("sftp", "SFTP_PORT"), ("flight", "FLIGHT_PORT")):
        text = (REPO / "quadlets" / f"{svc}.container").read_text()
        probes = [l for l in text.splitlines() if l.startswith(("HealthCmd=", "HealthStartupCmd="))]
        assert probes, f"{svc}.container has no healthcheck"
        for probe in probes:
            assert var in probe, f"{svc}.container probes a literal port: {probe}"
