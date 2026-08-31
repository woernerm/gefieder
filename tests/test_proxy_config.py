"""The proxy configuration is valid on its own, against a stub upstream.

Complements test_http.py, which drives the proxy of the running stack but cannot catch a
config nginx refuses to load: a proxy that never starts looks like one that is slow to
come up, and the failure surfaces as a connection reset.

Both templates are rendered and loaded exactly as the entrypoint does it, in a throwaway
container built from the proxy image, against stub upstreams. The failure this exists to
catch is a `proxy_pass` naming a host that does not resolve inside the pod's shared
network namespace, which aborts nginx with "host not found in upstream".

The certificate classes cover the other way the proxy alone can be down: production serves
HTTPS and nothing generates a certificate, so a missing one has to be reported by the
proxy itself rather than left in the log.
"""
import subprocess
import time
import uuid

import pytest

from conftest import CRUDMAN_PATH, GRAFANA_PATH, inspect_container

# The ports the two applications listen on inside the pod, which the proxy forwards to.
CRUDMAN_PORT = 8000
GRAFANA_PORT = 3000

# What the stub upstreams answer with, so a response can be attributed to the right one.
CRUDMAN_BODY = "stub-crudman"
GRAFANA_BODY = "stub-grafana"

# A path on the grafana stub that echoes a WebSocket handshake's headers back. The path
# is passed through unchanged, so the stub answers on the full one.
UPGRADE_PROBE = f"/{GRAFANA_PATH}/ws-probe"

# Where the certificate directory is mounted inside the container, fixed by the image;
# the host side is CERTIFICATE_PATH's to decide.
CERT_MOUNT = "/etc/nginx/proxy/certs"

# The opening words of the entrypoint's refusal, asserted verbatim because nginx's own
# certificate error mentions the file name too.
REFUSAL = "TLS certificate missing"

# podman copies the host's proxy variables into every container, and busybox wget honours
# them, so requests to the container's own loopback would go to the company proxy.
NO_PROXY_ENV = "--http-proxy=false"


def _stub_upstreams():
    """An nginx config serving a distinct body on each application port.

    Every container of the stack shares one network namespace, so binding these stubs to
    localhost in a single throwaway container reproduces the pod's layout faithfully.
    """
    return (
        f'server {{ listen {CRUDMAN_PORT}; '
        f'location / {{ return 200 "{CRUDMAN_BODY}"; }} }}\n'
        f'server {{ listen {GRAFANA_PORT}; '
        f'location / {{ return 200 "{GRAFANA_BODY}"; }} '
        # Echoes back what the proxy forwarded, for the WebSocket test.
        f'location = {UPGRADE_PROBE} '
        f'{{ return 200 "upgrade=[$http_upgrade] connection=[$http_connection]"; }} }}\n'
    )


@pytest.fixture(scope="module")
def fixtures(tmp_path_factory):
    """The stub upstream config and a throwaway certificate, ready to bind-mount.

    Generated on the host, the nginx image shipping no openssl, and mounted where the
    quadlet mounts the real one so the production template loads without it.
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
    entrypoint itself, the certificate check under test living there.

    Returns the CompletedProcess, or None when the entrypoint was still running at the
    timeout -- the healthy outcome when the check lets nginx start, since nginx then runs
    in the foreground forever.
    """
    env = ["-e", f"DEBUG={debug}"]
    if hint is not None:
        env += ["-e", f"CERTIFICATE_HINT={hint}"]
    # Named so it can be removed afterwards: a timeout kills the podman client, not the
    # container. --network none keeps it off the ports the running stack is using.
    name = f"certcheck-{uuid.uuid4().hex[:12]}"
    argv = ["podman", "run", "--rm", "--name", name, "--network", "none",
            NO_PROXY_ENV, "-v", f"{certs}:{CERT_MOUNT}:ro,z", *env, _proxy_image()]
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None
    finally:
        subprocess.run(["podman", "rm", "-f", "-i", name],
                       capture_output=True, timeout=60)


def _run_proxy(script, debug, fixtures):
    """Render the proxy config in a throwaway container and run `script` against it.

    Renders the requested template exactly as the image's entrypoint does, with the stub
    upstreams alongside it. The stub config and the certificate are bind-mounted rather
    than heredoc'd into the script, keeping the quoting to one level.

    Production's ssl_certificate is read from certs-effective/, which only the entrypoint's
    reorder_fullchain() populates, so that one function is pulled out of the real script
    and called here; without it every production-mode config would fail to load.
    """
    template = "http" if debug == "true" else "https"
    setup_steps = [
        "set -e",
        "mkdir -p /var/log/app /etc/nginx/conf.d",
        "cp /fixtures/upstreams.conf /etc/nginx/conf.d/upstreams.conf",
        f"export CRUDMAN_PATH={CRUDMAN_PATH} GRAFANA_PATH={GRAFANA_PATH} DEBUG={debug}",
    ]
    if debug != "true":
        setup_steps += [
            "sed -n '/^reorder_fullchain() {/,/^}/p' /etc/nginx/proxy/entrypoint.sh"
            " > /tmp/reorder_fullchain.sh",
            ". /tmp/reorder_fullchain.sh",
            "reorder_fullchain",
        ]
    setup_steps.append(
        "envsubst '${CRUDMAN_PATH} ${GRAFANA_PATH}'"
        f" < /etc/nginx/proxy/{template}.conf.template > /etc/nginx/conf.d/default.conf"
    )
    # Both templates include the shared maps and locations fragments.
    setup_steps += [
        "for f in maps locations; do envsubst '${CRUDMAN_PATH} ${GRAFANA_PATH}'"
        " < /etc/nginx/proxy/$f.conf.template > /etc/nginx/proxy/$f.conf; done"
    ]
    setup = "; ".join(setup_steps)
    return subprocess.run(
        ["podman", "run", "--rm", NO_PROXY_ENV,
         "-v", f"{fixtures}:/fixtures:ro,z",
         "-v", f"{fixtures / 'certs'}:{CERT_MOUNT}:ro,z",
         "--entrypoint", "sh", _proxy_image(), "-c", setup + "\n" + script],
        capture_output=True, text=True, timeout=120,
    )


def _proxy_image():
    """The image the running proxy was started from.

    Taken from the container, so these checks run against the artifact the stack runs.
    """
    info = inspect_container("proxy")
    if info is None:
        pytest.skip("proxy container does not exist")
    return info["ImageName"]


# Start nginx, then poll the given path until it answers. busybox wget's -S writes the
# response headers to stderr, which is how the redirect test reads them.
FETCH = """
nginx
for i in $(seq 1 40); do
    wget -qS -O /tmp/body {args} http://{host}{path} 2>/tmp/head && break
    sleep 0.25
done
echo "--- headers ---"
cat /tmp/head
echo "--- body ---"
cat /tmp/body 2>/dev/null || true
"""


def _fetch(path, args="", host="127.0.0.1"):
    """The FETCH script for one request; `host` is what the IPv6 test varies."""
    return FETCH.format(path=path, args=args, host=host)


class TestConfigLoads:
    """nginx accepts the rendered configuration.

    `nginx -t` rejects the whole config when a `proxy_pass` host does not resolve, which
    is the regression under test: the proxy never starts, and every request comes back as
    a connection reset rather than an HTTP error.
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
        # Separate from the load check, so a regression names its cause. Container names
        # do not resolve inside the pod's shared network namespace.
        result = _run_proxy("nginx -t", debug, fixtures)
        assert "host not found in upstream" not in result.stderr, (
            "a proxy_pass addresses a service by container name; inside the pod's shared "
            f"network namespace the services are on localhost:\n{result.stderr}"
        )


class TestMissingCertificate:
    """Production refuses to start without a certificate, and says which file is missing.

    Nothing generates a self-signed certificate, so this message is all that stands
    between an operator and a proxy that is simply down. Development mode serves plain
    HTTP and never reads the directory, which is what lets dev.sh run without one.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def empty_certs(cls, tmp_path_factory):
        """The directory the quadlet creates before any certificate is put in."""
        return tmp_path_factory.mktemp("empty_certs")

    @pytest.mark.parametrize("missing", ["fullchain.pem", "privkey.pem"])
    def test_production_shall_refuse_to_start_without_a_certificate(
        self, missing, tmp_path_factory
    ):
        # One file at a time: a check looking only for the other would let nginx start
        # and fail back inside the log.
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
        # error, so matching the name alone would pass with the check gone.
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
        # The path inside the container says nothing about where the operator puts the
        # files, so the quadlet passes the host directory in as CERTIFICATE_HINT.
        hint = "/etc/pki/somewhere-specific"
        result = _run_entrypoint("false", empty_certs, hint=hint)
        assert result is not None, "the proxy started although no certificate is present"
        assert hint in result.stderr, (
            f"the message does not name the host certificate directory:\n{result.stderr}"
        )

    def test_development_shall_start_without_any_certificate(self, empty_certs):
        # DEBUG=true serves plain HTTP, so nginx runs on in the foreground -- which is
        # what the timeout reports as success.
        result = _run_entrypoint("true", empty_certs, timeout=20)
        assert result is None, (
            "the proxy exited in development mode, where it needs no certificate:\n"
            f"{result.stdout}\n{result.stderr}"
        )


class TestCertificateWiring:
    """The running proxy gets its certificate directory from the host, as the quadlet says.

    CERTIFICATE_PATH is substituted at build time and resolved by systemd, so a rename or
    a dropped variable would leave the mount pointing somewhere unintended.
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
        # A host directory, not a named volume: certificates are renewed by copying.
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
        # What the refusal message prints. Read from the container, so a quadlet that
        # renders an empty value is caught.
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


# What the probes below ask the running proxy. openssl runs inside the container, which
# ships one for the entrypoint's own use, so the test host needs no client tooling.
PROBE_SUBJECT = ("echo | openssl s_client -connect 127.0.0.1:443 2>/dev/null "
                 "| openssl x509 -noout -subject 2>/dev/null")
PROBE_CHAIN = "echo | openssl s_client -showcerts -connect 127.0.0.1:443 2>/dev/null"


def _serve_certs(certs, probe):
    """Start the proxy image against `certs` and run `probe` inside it once TLS answers.

    Returns the probe's stripped stdout, or None if it never answered, and the container's
    stderr log. Polls because nginx needs a moment to bind, and a certificate the proxy
    refuses leaves it never answering -- the None the callers assert on.
    """
    name = f"certorder-{uuid.uuid4().hex[:12]}"
    subprocess.run(
        ["podman", "run", "-d", "--rm", "--name", name, "--network", "none",
         NO_PROXY_ENV, "-e", "DEBUG=false", "-v", f"{certs}:{CERT_MOUNT}:ro,z",
         _proxy_image()],
        capture_output=True, text=True, check=True,
    )
    try:
        answer = None
        for _ in range(40):
            result = subprocess.run(
                ["podman", "exec", name, "sh", "-c", probe],
                capture_output=True, text=True,
            )
            if result.returncode == 0 and result.stdout.strip():
                answer = result.stdout.strip()
                break
            time.sleep(0.25)
        logs = subprocess.run(
            ["podman", "logs", name], capture_output=True, text=True
        ).stderr
        return answer, logs
    finally:
        subprocess.run(["podman", "rm", "-f", "-i", name],
                       capture_output=True, timeout=60)


def _leaf_subject_served(certs):
    """The subject of the certificate the proxy presents, and its log."""
    return _serve_certs(certs, PROBE_SUBJECT)


class TestCertificateOrder:
    """fullchain.pem loads regardless of which order it lists its certificates in.

    nginx pairs privkey.pem with whichever certificate comes first and aborts ("key values
    mismatch") if that is not the leaf, while a file converted from a CA's p7b can list
    the root first. The entrypoint reorders the chain itself (reorder_fullchain() in
    entrypoint.sh); these checks build both orders and confirm each serves the leaf, that
    only the misordered file is reported, and that the host's own file is untouched.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def chain(cls, tmp_path_factory):
        """A leaf certificate signed by a throwaway root CA, laid out in both orders."""
        path = tmp_path_factory.mktemp("cert_order")
        root_key, root_pem = path / "root.key", path / "root.pem"
        leaf_key, leaf_csr, leaf_pem = path / "leaf.key", path / "leaf.csr", path / "leaf.pem"
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
             "-subj", "/CN=Test Root CA", "-keyout", str(root_key), "-out", str(root_pem)],
            capture_output=True, check=True,
        )
        subprocess.run(
            ["openssl", "req", "-newkey", "rsa:2048", "-nodes",
             "-subj", "/CN=localhost", "-keyout", str(leaf_key), "-out", str(leaf_csr)],
            capture_output=True, check=True,
        )
        subprocess.run(
            ["openssl", "x509", "-req", "-in", str(leaf_csr), "-CA", str(root_pem),
             "-CAkey", str(root_key), "-CAcreateserial", "-days", "1", "-out", str(leaf_pem)],
            capture_output=True, check=True,
        )
        leaf_text, root_text = leaf_pem.read_text(), root_pem.read_text()

        def certs_dir(name, order):
            d = path / name
            d.mkdir()
            (d / "fullchain.pem").write_text("".join(order))
            (d / "privkey.pem").write_text(leaf_key.read_text())
            return d

        # "reversed" is what a root-first p7b conversion produces, the case the
        # reordering exists to fix.
        return {
            "correct": certs_dir("correct", [leaf_text, root_text]),
            "reversed": certs_dir("reversed", [root_text, leaf_text]),
        }

    @pytest.mark.parametrize("order", ["correct", "reversed"])
    def test_the_leaf_shall_be_served_regardless_of_file_order(self, order, chain):
        subject, logs = _leaf_subject_served(chain[order])
        assert subject == "subject=CN=localhost", (
            f"nginx did not serve the leaf certificate for the {order} ordering:\n{logs}"
        )

    def test_reordering_shall_only_be_logged_when_the_order_was_wrong(self, chain):
        _, correct_logs = _leaf_subject_served(chain["correct"])
        _, reversed_logs = _leaf_subject_served(chain["reversed"])
        assert "reorder" not in correct_logs.lower(), (
            f"a correctly-ordered fullchain.pem logged a reordering message:\n{correct_logs}"
        )
        assert "reorder" in reversed_logs.lower(), (
            f"a root-first fullchain.pem was not reported as reordered:\n{reversed_logs}"
        )

    def test_the_hosts_own_file_shall_not_be_modified(self, chain):
        certs = chain["reversed"]
        before = (certs / "fullchain.pem").read_bytes()
        _leaf_subject_served(certs)
        after = (certs / "fullchain.pem").read_bytes()
        assert before == after, (
            "the entrypoint modified the operator's fullchain.pem; it must only ever "
            "write its reordered copy to certs-effective/"
        )


class TestConvertedBundles:
    """A fullchain.pem produced by converting a bundle the CA handed out is usable.

    This is how the misordered files TestCertificateOrder covers arise, and the
    conversions interleave `subject=`/`issuer=` headers, `Bag Attributes` blocks and blank
    lines between the PEM blocks.

    Those lines are what a splitter gets wrong: awk truncates a file when a `print >`
    reopens it after close(), so one that keeps writing past END CERTIFICATE empties the
    certificate it just finished. Every certificate is then unparseable and the entrypoint
    passes the file through untouched, doing nothing on the input it exists for. A bundle
    of plain concatenated certificates would never show it, hence this class.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def bundles(cls, tmp_path_factory):
        """Root-first fullchain.pem files converted from a real .p7b and a real .p12.

        A three-level hierarchy (root CA -> intermediate CA -> leaf), root-first because
        that is the ordering nginx refuses, converted with the commands an operator runs.
        """
        path = tmp_path_factory.mktemp("converted")

        def run(*argv):
            subprocess.run([str(a) for a in argv], capture_output=True, check=True)

        root_key, root_pem = path / "root.key", path / "root.pem"
        inter_key, inter_csr, inter_pem = (
            path / "inter.key", path / "inter.csr", path / "inter.pem")
        leaf_key, leaf_csr, leaf_pem = (
            path / "leaf.key", path / "leaf.csr", path / "leaf.pem")

        run("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
            "-subj", "/CN=Test Root CA", "-keyout", root_key, "-out", root_pem)
        # Without CA:TRUE openssl will not treat it as an issuer.
        ext = path / "ca.ext"
        ext.write_text("basicConstraints=critical,CA:TRUE\n"
                       "keyUsage=critical,keyCertSign,cRLSign\n")
        run("openssl", "req", "-newkey", "rsa:2048", "-nodes",
            "-subj", "/CN=Test Intermediate CA", "-keyout", inter_key, "-out", inter_csr)
        run("openssl", "x509", "-req", "-in", inter_csr, "-CA", root_pem,
            "-CAkey", root_key, "-CAcreateserial", "-days", "1",
            "-extfile", ext, "-out", inter_pem)
        run("openssl", "req", "-newkey", "rsa:2048", "-nodes",
            "-subj", "/CN=localhost", "-keyout", leaf_key, "-out", leaf_csr)
        run("openssl", "x509", "-req", "-in", leaf_csr, "-CA", inter_pem,
            "-CAkey", inter_key, "-CAcreateserial", "-days", "1", "-out", leaf_pem)

        def root_first(text):
            """Put the certificates of an annotated bundle in root-first order.

            Each chunk keeps the headers preceding its own PEM block, so the result still
            looks like what the conversion emits. Leaf-first is the one ordering under
            which a splitter that loses the certificates still works by accident.
            """
            end = "-----END CERTIFICATE-----\n"
            chunks = [c + end for c in text.split(end) if "BEGIN CERTIFICATE" in c]
            return "".join(reversed(chunks))

        made = {}

        # crl2pkcs7 keeps the order it is given, so this comes out root-first already.
        p7b, p7b_pem = path / "chain.p7b", path / "p7b.pem"
        run("openssl", "crl2pkcs7", "-nocrl", "-certfile", root_pem,
            "-certfile", inter_pem, "-certfile", leaf_pem, "-out", p7b)
        run("openssl", "pkcs7", "-in", p7b, "-print_certs", "-out", p7b_pem)
        made["pkcs7"] = p7b_pem.read_text()

        # `openssl pkcs12 -nokeys` adds Bag Attributes blocks. The export puts the key's
        # own certificate first, so it is flipped.
        p12, p12_pem = path / "chain.p12", path / "p12.pem"
        ca_chain = path / "ca_chain.pem"
        ca_chain.write_text(inter_pem.read_text() + root_pem.read_text())
        run("openssl", "pkcs12", "-export", "-inkey", leaf_key, "-in", leaf_pem,
            "-certfile", ca_chain, "-passout", "pass:", "-out", p12)
        run("openssl", "pkcs12", "-in", p12, "-nokeys", "-passin", "pass:", "-out", p12_pem)
        made["pkcs12"] = root_first(p12_pem.read_text())

        result = {}
        for name, text in made.items():
            d = path / name
            d.mkdir()
            (d / "fullchain.pem").write_text(text)
            (d / "privkey.pem").write_text(leaf_key.read_text())
            result[name] = d
        return result

    @pytest.mark.parametrize("fmt", ["pkcs7", "pkcs12"])
    def test_the_converted_bundle_shall_carry_annotations_between_certificates(
        self, fmt, bundles
    ):
        # Guards the fixture: were openssl to emit a bare concatenation, the checks below
        # would pass while testing nothing of the kind.
        text = (bundles[fmt] / "fullchain.pem").read_text()
        body = text.split("-----END CERTIFICATE-----", 1)[1]
        between = body.split("-----BEGIN CERTIFICATE-----", 1)[0]
        assert between.strip() or "\n\n" in body, (
            f"the {fmt} bundle holds nothing between its certificates, so it cannot show "
            "the splitter mishandling those lines"
        )

    @pytest.mark.parametrize("fmt", ["pkcs7", "pkcs12"])
    def test_the_leaf_shall_be_served_from_a_converted_bundle(self, fmt, bundles):
        subject, logs = _leaf_subject_served(bundles[fmt])
        assert subject == "subject=CN=localhost", (
            f"the proxy did not serve the leaf from a {fmt}-converted bundle. A splitter "
            f"that loses the certificates leaves the file passed through unreordered, so "
            f"nginx never starts:\n{logs}"
        )

    @pytest.mark.parametrize("fmt", ["pkcs7", "pkcs12"])
    def test_the_whole_chain_shall_be_served_from_a_converted_bundle(self, fmt, bundles):
        # Every certificate has to survive the split: a chain that lost its intermediate
        # still starts nginx, and only clients lacking that intermediate fail.
        chain, logs = _serve_certs(bundles[fmt], PROBE_CHAIN)
        assert chain is not None, f"the proxy served no chain at all:\n{logs}"
        assert chain.count("BEGIN CERTIFICATE") == 3, (
            f"the {fmt}-converted bundle held 3 certificates but the proxy served "
            f"{chain.count('BEGIN CERTIFICATE')}; the splitter dropped or corrupted one"
        )


class TestRoutingToUpstreams:
    """Each application path reaches its own upstream.

    The stubs' distinct bodies also catch a config that loads but routes both paths to the
    same service.
    """

    @pytest.mark.parametrize("path,expected", [
        (f"/{CRUDMAN_PATH}/", CRUDMAN_BODY),
        (f"/{GRAFANA_PATH}/", GRAFANA_BODY),
    ])
    def test_each_path_shall_reach_its_own_upstream(self, path, expected, fixtures):
        # Plain HTTP keeps the assertion about routing rather than TLS.
        result = _run_proxy(_fetch(path), "true", fixtures)
        assert expected in result.stdout, (
            f"{path} did not reach its upstream:\n{result.stdout}\n{result.stderr}"
        )

    def test_the_root_shall_redirect_to_the_admin_panel(self, fixtures):
        # --spider does not follow the redirect (busybox wget has no --max-redirect).
        result = _run_proxy(_fetch("/", args="--spider"), "true", fixtures)
        assert "302" in result.stdout, result.stdout + result.stderr
        # A bare path: nginx would build a URL from the port it listens on inside the pod,
        # sending a client that arrived on any other port where nothing listens.
        assert f"Location: /{CRUDMAN_PATH}/" in result.stdout, (
            "the redirect is absolute; it drops the port the client is talking to:\n"
            + result.stdout + result.stderr
        )


class TestWebSockets:
    """A WebSocket handshake survives the hop to Grafana.

    Upgrade and Connection are hop-by-hop headers nginx drops unless told otherwise, and
    its default HTTP/1.0 upstream cannot carry an upgrade at all. Grafana Live then fails
    while every ordinary page loads, so nothing else here would notice.
    """

    def test_the_upgrade_headers_shall_reach_grafana(self, fixtures):
        args = "--header 'Upgrade: websocket' --header 'Connection: Upgrade'"
        result = _run_proxy(_fetch(UPGRADE_PROBE, args=args), "true", fixtures)
        assert "upgrade=[websocket]" in result.stdout, (
            "nginx dropped the Upgrade header; Grafana Live cannot connect:\n"
            + result.stdout + result.stderr
        )
        assert "connection=[upgrade]" in result.stdout.lower(), (
            "nginx dropped the Connection: upgrade header; Grafana Live cannot connect:\n"
            + result.stdout + result.stderr
        )


class TestAddressFamilies:
    """The proxy answers over IPv6 as well as IPv4.

    A DNS answering with an AAAA record leaves a browser never trying the IPv4 address, so
    an IPv4-only listener is unreachable while every check from the server itself passes.
    """

    @pytest.mark.parametrize("debug", ["true", "false"])
    def test_the_proxy_shall_listen_on_ipv6(self, debug, fixtures):
        # The redirect at "/" is served by the proxy itself, so this needs no upstream,
        # and any 30x means the connection was accepted. --spider keeps wget from
        # following it; --no-check-certificate lets production's redirect into HTTPS
        # resolve against the throwaway certificate.
        args = "--spider --no-check-certificate"
        result = _run_proxy(_fetch("/", args=args, host="[::1]"), debug, fixtures)
        assert "HTTP/1.1 30" in result.stdout, (
            "the proxy did not answer over IPv6:\n" + result.stdout + result.stderr
        )
