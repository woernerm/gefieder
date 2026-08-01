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
"""
import subprocess

import pytest

from conftest import CRUDMAN_PATH, GRAFANA_PATH, inspect_container

# The ports the two applications listen on inside the pod, which the proxy forwards to.
CRUDMAN_PORT = 8000
GRAFANA_PORT = 3000

# What the stub upstreams answer with, so a response can be attributed to the right one.
CRUDMAN_BODY = "stub-crudman"
GRAFANA_BODY = "stub-grafana"


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
         "-v", f"{fixtures / 'certs'}:/etc/nginx/proxy/certs:ro,z",
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
