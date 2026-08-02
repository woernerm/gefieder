"""The proxy configuration is valid on its own, against a stub upstream.

This complements test_http.py, which drives the proxy of the running stack. That suite
cannot catch a config nginx refuses to load: a proxy that never starts is indistinguishable
from one that is merely slow to come up, and the failure surfaces as a connection reset
rather than as a bad HTTP response.

Both templates are rendered and loaded here exactly as the entrypoint does it, in a
throwaway container built from the proxy image. The upstreams are stubs -- a plain nginx
serving a known body on the app ports -- so the check needs no crudman, no grafana, no
database and no network access beyond the container's own loopback. The failure it exists
to catch is a `proxy_pass` naming a host that does not resolve inside the pod's shared
network namespace, which aborts nginx at startup with "host not found in upstream".

The certificate classes below cover the other way the proxy can be the only service that
is down: production serves HTTPS and nothing generates a certificate for the operator, so
a missing one has to be reported by the proxy itself rather than left in the log.
"""
import subprocess
import uuid

import pytest

from conftest import CRUDMAN_PATH, GRAFANA_PATH, inspect_container

# The ports the two applications listen on inside the pod, which the proxy forwards to.
CRUDMAN_PORT = 8000
GRAFANA_PORT = 3000

# What the stub upstreams answer with, so a response can be attributed to the right one.
CRUDMAN_BODY = "stub-crudman"
GRAFANA_BODY = "stub-grafana"

# Where the certificate directory is mounted inside the container. Fixed by the image (the
# conf templates reference it), unlike the host side, which CERTIFICATE_PATH decides.
CERT_MOUNT = "/etc/nginx/proxy/certs"

# The opening words of the entrypoint's refusal. Asserted verbatim because nginx's own
# certificate error mentions the file name too: without this, a check that had been
# removed would still look like a pass.
REFUSAL = "TLS certificate missing"


def _stub_upstreams():
    """An nginx config serving a distinct body on each application port.

    Stands in for crudman and grafana. Because every container of the stack shares one
    network namespace, the real services are reachable on localhost -- so binding these
    stubs to localhost inside a single throwaway container reproduces the pod's network
    layout faithfully.
    """
    return (
        f'server {{ listen {CRUDMAN_PORT}; '
        f'location / {{ return 200 "{CRUDMAN_BODY}"; }} }}\n'
        f'server {{ listen {GRAFANA_PORT}; '
        f'location / {{ return 200 "{GRAFANA_BODY}"; }} }}\n'
    )


@pytest.fixture(scope="module")
def fixtures(tmp_path_factory):
    """The stub upstream config and a throwaway certificate, ready to bind-mount.

    The certificate is generated on the host because the nginx image ships no openssl, and
    is mounted where the proxy quadlet mounts the real one, so the production template can
    be loaded without the deployment's own certificate being present.
    """
    path = tmp_path_factory.mktemp("proxy_fixtures")
    (path / "upstreams.conf").write_text(_stub_upstreams())
    certs = path / "certs"
    certs.mkdir()
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
         "-subj", "/CN=localhost",
         "-keyout", str(certs / "privkey.pem"), "-out", str(certs / "fullchain.pem")],
        capture_output=True, check=True,
    )
    return path


def _run_entrypoint(debug, certs, hint=None, timeout=60):
    """Run the image's own entrypoint against `certs`, mounted where the quadlet mounts it.

    Unlike _run_proxy, which renders a template the way the entrypoint does, this runs the
    entrypoint itself: the certificate check under test lives there, so a copy of it here
    would assert nothing.

    Returns the CompletedProcess, or None when the entrypoint was still running at the
    timeout -- which is the healthy outcome whenever the check lets nginx start, because
    nginx then runs in the foreground forever. Tests that expect a refusal assert on the
    result; the one that expects a start asserts that there is none.
    """
    env = ["-e", f"DEBUG={debug}"]
    if hint is not None:
        env += ["-e", f"CERTIFICATE_HINT={hint}"]
    # Named so the container can be removed by name afterwards: a timeout kills the podman
    # client, not the container it started, and the one case that reaches nginx would
    # otherwise leave it running for as long as the machine is up.
    # --network none: nothing here serves traffic, and it keeps the container off the ports
    # the running stack is using.
    name = f"certcheck-{uuid.uuid4().hex[:12]}"
    argv = ["podman", "run", "--rm", "--name", name, "--network", "none",
            "-v", f"{certs}:{CERT_MOUNT}:ro,z", *env, _proxy_image()]
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None
    finally:
        subprocess.run(["podman", "rm", "-f", "-i", name],
                       capture_output=True, timeout=60)


def _run_proxy(script, debug, fixtures):
    """Render the proxy config in a throwaway container and run `script` against it.

    Renders the requested template exactly as the image's entrypoint does (same envsubst
    variable list), with the stub upstreams dropped in alongside it, then hands over to
    `script`. Returns the CompletedProcess so a caller can assert on exit status and
    output both.

    The stub config and the certificate are written on the host and bind-mounted, rather
    than heredoc'd into the shell script: the nginx image ships no openssl, and keeping
    the generated files out of the script keeps the quoting to one level.
    """
    template = "http" if debug == "true" else "https"
    setup = "; ".join([
        "set -e",
        "mkdir -p /var/log/app /etc/nginx/conf.d",
        "cp /fixtures/upstreams.conf /etc/nginx/conf.d/upstreams.conf",
        f"export CRUDMAN_PATH={CRUDMAN_PATH} GRAFANA_PATH={GRAFANA_PATH} DEBUG={debug}",
        "envsubst '${CRUDMAN_PATH} ${GRAFANA_PATH}'"
        f" < /etc/nginx/proxy/{template}.conf.template > /etc/nginx/conf.d/default.conf",
    ])
    return subprocess.run(
        ["podman", "run", "--rm",
         "-v", f"{fixtures}:/fixtures:ro,z",
         "-v", f"{fixtures / 'certs'}:{CERT_MOUNT}:ro,z",
         "--entrypoint", "sh", _proxy_image(), "-c", setup + "\n" + script],
        capture_output=True, text=True, timeout=120,
    )


def _proxy_image():
    """The image the running proxy was started from.

    Taken from the container rather than rebuilt from a name of our own, so these checks
    always run against exactly the artifact the stack is running.
    """
    info = inspect_container("proxy")
    if info is None:
        pytest.skip("proxy container does not exist")
    return info["ImageName"]


# Start nginx, then poll the given path until it answers. busybox wget writes the response
# headers to stderr with -S, which is how the redirect test sees the status line and the
# Location header without following the redirect.
FETCH = """
nginx
for i in $(seq 1 40); do
    wget -qS -O /tmp/body {args} http://127.0.0.1{path} 2>/tmp/head && break
    sleep 0.25
done
echo "--- headers ---"
cat /tmp/head
echo "--- body ---"
cat /tmp/body 2>/dev/null || true
"""


class TestConfigLoads:
    """nginx accepts the rendered configuration.

    `nginx -t` resolves every `proxy_pass` host at load time and rejects the whole config
    when one does not resolve, which is exactly the regression under test: the proxy then
    never starts, and every request is answered with a connection reset rather than an
    HTTP error, so the stack looks up while nothing is served.
    """

    @pytest.mark.parametrize("debug", ["true", "false"])
    def test_the_rendered_config_shall_load(self, debug, fixtures):
        result = _run_proxy("nginx -t", debug, fixtures)
        assert result.returncode == 0, (
            f"nginx rejected the {'dev' if debug == 'true' else 'production'} config:\n"
            f"{result.stderr}"
        )

    @pytest.mark.parametrize("debug", ["true", "false"])
    def test_no_upstream_shall_be_addressed_by_container_name(self, debug, fixtures):
        # Asserted separately from the load check so a regression names its cause rather
        # than only reporting that the config did not load. Every container shares the
        # pod's network namespace, in which container names do not resolve -- the services
        # are reachable on localhost.
        result = _run_proxy("nginx -t", debug, fixtures)
        assert "host not found in upstream" not in result.stderr, (
            "a proxy_pass addresses a service by container name; inside the pod's shared "
            f"network namespace the services are on localhost:\n{result.stderr}"
        )


class TestMissingCertificate:
    """Production refuses to start without a certificate, and says which file is missing.

    The installer no longer generates a self-signed certificate, so this message is the
    only thing standing between an operator and a proxy that is simply down. Development
    mode must stay unaffected: it serves plain HTTP and never reads the directory, which
    is what lets dev.sh and this suite run without one.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def empty_certs(cls, tmp_path_factory):
        """The certificate directory the quadlet creates before any certificate is put in."""
        return tmp_path_factory.mktemp("empty_certs")

    @pytest.mark.parametrize("missing", ["fullchain.pem", "privkey.pem"])
    def test_production_shall_refuse_to_start_without_a_certificate(
        self, missing, tmp_path_factory
    ):
        # Each file is removed on its own: a check that only looked for one of them would
        # let nginx start and then fail on the other, back inside the log.
        certs = tmp_path_factory.mktemp(f"certs_without_{missing.split('.')[0]}")
        for name in ("fullchain.pem", "privkey.pem"):
            if name != missing:
                (certs / name).write_text("")
        result = _run_entrypoint("false", certs)
        assert result is not None, f"the proxy started although {missing} is missing"
        assert result.returncode != 0, (
            f"the proxy exited cleanly although {missing} is missing:\n{result.stdout}"
        )
        # Our own wording, not just the file name: nginx names fullchain.pem in its own
        # "cannot load certificate" error, so matching the name alone would pass even
        # with the check gone -- which is the failure this test exists to prevent.
        assert REFUSAL in result.stderr, (
            f"the proxy failed without stating why; the operator is left with nginx's "
            f"error instead of a message naming {missing}:\n{result.stderr}"
        )
        assert missing in result.stderr, (
            f"the message does not name the missing {missing}:\n{result.stderr}"
        )

    def test_the_refusal_shall_name_the_certificate_directory_on_the_host(
        self, empty_certs
    ):
        # The path inside the container says nothing about where the operator has to put
        # the files, so the quadlet passes the host directory in; without it the operator
        # is left guessing. See CERTIFICATE_HINT in quadlets/proxy.container.
        hint = "/etc/pki/somewhere-specific"
        result = _run_entrypoint("false", empty_certs, hint=hint)
        assert result is not None, "the proxy started although no certificate is present"
        assert hint in result.stderr, (
            f"the message does not name the host certificate directory:\n{result.stderr}"
        )

    def test_development_shall_start_without_any_certificate(self, empty_certs):
        # DEBUG=true serves plain HTTP, so an empty directory must not stop the proxy.
        # nginx then runs in the foreground, which is what the timeout reports as success.
        result = _run_entrypoint("true", empty_certs, timeout=20)
        assert result is None, (
            "the proxy exited in development mode, where it needs no certificate:\n"
            f"{result.stdout}\n{result.stderr}"
        )


class TestCertificateWiring:
    """The running proxy gets its certificate directory from the host, as the quadlet says.

    CERTIFICATE_PATH is substituted into the quadlet at build time and resolved by systemd,
    so a rename or a dropped variable would leave the mount pointing somewhere unintended.
    Both are read back from the container the stack is actually running.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def proxy(cls):
        info = inspect_container("proxy")
        if info is None:
            pytest.skip("proxy container does not exist")
        return info

    def test_the_certificate_directory_shall_be_mounted_from_the_host(self, proxy):
        mounts = {m["Destination"]: m for m in proxy.get("Mounts", [])}
        mount = mounts.get(CERT_MOUNT)
        assert mount is not None, (
            f"the proxy has no mount at {CERT_MOUNT}; the certificate would have to be "
            f"baked into the image. Mounted: {sorted(mounts)}"
        )
        # A bind mount of a real host directory, not a named volume: certificates are
        # renewed by copying files, which a volume would make needlessly awkward.
        assert mount.get("Type") == "bind", (
            f"the certificate directory is a {mount.get('Type')}, not a host directory"
        )
        assert mount["Source"].startswith("/"), (
            f"the mount source is not an absolute host path: {mount['Source']!r}. "
            "A '%h' left here means systemd did not resolve CERTIFICATE_PATH."
        )

    def test_the_certificate_directory_shall_be_read_only(self, proxy):
        mounts = {m["Destination"]: m for m in proxy.get("Mounts", [])}
        mount = mounts.get(CERT_MOUNT)
        assert mount is not None, f"the proxy has no mount at {CERT_MOUNT}"
        assert mount.get("RW") is False, (
            "the proxy can write to the certificate directory; it only ever reads it"
        )

    def test_the_host_certificate_directory_shall_be_passed_to_the_entrypoint(self, proxy):
        # What the refusal message prints. Read from the container so a quadlet that
        # renders an empty value (a variable missing from the envsubst list) is caught.
        env = dict(
            item.split("=", 1)
            for item in proxy["Config"].get("Env", []) if "=" in item
        )
        hint = env.get("CERTIFICATE_HINT", "")
        assert hint.startswith("/"), (
            "CERTIFICATE_HINT is not an absolute host path "
            f"({hint!r}); the proxy could not name the certificate directory."
        )
        mounts = {m["Destination"]: m for m in proxy.get("Mounts", [])}
        assert hint == mounts[CERT_MOUNT]["Source"], (
            "CERTIFICATE_HINT names a different directory than the one mounted; the "
            "refusal message would send the operator to the wrong place."
        )


class TestRoutingToUpstreams:
    """Each application path reaches its own upstream.

    Also guards against the mirror-image mistake of a config that loads but routes both
    paths to the same service, which the stubs' distinct bodies detect.
    """

    @pytest.mark.parametrize("path,expected", [
        (f"/{CRUDMAN_PATH}/", CRUDMAN_BODY),
        (f"/{GRAFANA_PATH}/", GRAFANA_BODY),
    ])
    def test_each_path_shall_reach_its_own_upstream(self, path, expected, fixtures):
        # Plain HTTP (DEBUG=true) keeps the assertion about routing rather than TLS.
        result = _run_proxy(FETCH.format(path=path, args=""), "true", fixtures)
        assert expected in result.stdout, (
            f"{path} did not reach its upstream:\n{result.stdout}\n{result.stderr}"
        )

    def test_the_root_shall_redirect_to_the_admin_panel(self, fixtures):
        # --spider issues the request without following the redirect, so the 302 and its
        # Location header are what gets asserted. (busybox wget has no --max-redirect.)
        result = _run_proxy(FETCH.format(path="/", args="--spider"), "true", fixtures)
        assert "302" in result.stdout, result.stdout + result.stderr
        assert f"/{CRUDMAN_PATH}/" in result.stdout, result.stdout + result.stderr
