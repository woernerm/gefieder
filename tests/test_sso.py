"""Single sign-on, in both the state it ships in and the state it is meant to reach.

Two halves. The first checks that the plumbing is inert while OIDC_ENABLED is false, which
is the state every existing installation stays in after an upgrade. The second turns it on
against the stand-in identity provider run-tests.sh runs inside the pod, signs in through
the proxy the way a browser would, and asserts that both services end up with the person
the provider described and the rights their role grants.

The stand-in provider is a real OpenID Connect server, so the exchange under test is the
real one: discovery, an authorization redirect, a code, a token and the claims inside it.
What it does not reproduce is a directory's own behaviour -- consent screens, conditional
access, an expired client secret -- so a first sign-in against a real provider still has to
be tried by hand.
"""
import os
import subprocess
import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from conftest import (
    APP_CONFIG_DIR, BASE_URL, CRUDMAN_LOGIN, CRUDMAN_PATH, GRAFANA_PATH, OIDC_ISSUER,
    RESTART_TIMEOUT, SECRETS, SSO_GROUP_PREFIX, VERIFY_TLS, inspect_container, podman,
)

# The host name the stack was started with, which Grafana has to be told about: its own
# default is "localhost", and it builds the sign-in callback address out of it.
SERVER_NAME = os.environ.get("SERVER_NAME", "localhost")

# The services that take part in single sign-on, and so mount the client secret.
SSO_CONTAINERS = ["crudman", "grafana"]

# The person the stand-in provider describes, and the role it grants them. Editor is the
# middle of the three, so both a granted and a withheld permission can be asserted.
SSO_USER = "kim"
SSO_ROLE_GROUP = f"{SSO_GROUP_PREFIX}editor"


def _django(script):
    """Run a snippet inside the crudman container and return what it printed.

    Through uv, because the application's dependencies live in its project environment
    rather than in the interpreter that is first on PATH. Quietly, because the shell
    announces the models it imported for itself before running anything.
    """
    return podman(
        "exec", "crudman",
        "uv", "run", "--project", "/crudman",
        "python", "manage.py", "shell", "-v", "0", "-c", script,
    ).strip()


def _set_single_sign_on(enabled):
    """Rewrite OIDC_ENABLED in the runtime.env the services read, and restart them.

    The same edit an operator makes to switch single sign-on on, followed by the same
    restart -- which is the only way the change takes, because a container reads its
    environment file once, when it starts.
    """
    path = os.path.join(APP_CONFIG_DIR, "runtime.env")
    with open(path, encoding="utf-8") as fh:
        lines = fh.readlines()
    with open(path, "w", encoding="utf-8") as fh:
        for line in lines:
            if line.startswith("OIDC_ENABLED="):
                line = f"OIDC_ENABLED={'true' if enabled else 'false'}\n"
            fh.write(line)

    subprocess.run(
        ["systemctl", "--user", "restart", "crudman.service", "grafana.service"],
        check=True,
    )

    # crudman's unit reports ready only once its healthcheck passes, but Grafana's does
    # not, so wait for both to answer rather than trusting systemctl's return.
    deadline = time.time() + RESTART_TIMEOUT
    with httpx.Client(base_url=BASE_URL, verify=VERIFY_TLS, trust_env=False,
                      follow_redirects=False, timeout=10) as client:
        while time.time() < deadline:
            try:
                if all(client.get(url).status_code < 500
                       for url in (CRUDMAN_LOGIN, f"/{GRAFANA_PATH}/login")):
                    return
            except httpx.HTTPError:
                pass
            time.sleep(2)
    raise AssertionError("the services did not come back after the restart")


def _derived_root_url(**env):
    """The public address the grafana image's entrypoint settles on for this environment.

    Runs the real entrypoint in a throwaway container with only its last line -- the
    handover to Grafana's own /run.sh -- replaced by a print, the way test_proxy_config.py
    runs pieces of the proxy entrypoint. Reading the address off a started Grafana instead
    would cost a server startup per case and assert nothing more.
    """
    info = inspect_container("grafana")
    if info is None:
        pytest.skip("grafana container does not exist")
    argv = ["podman", "run", "--rm", "--network", "none"]
    for name, value in env.items():
        argv += ["-e", f"{name}={value}"]
    argv += ["--entrypoint", "sh", info["ImageName"], "-c",
             "sed 's|^exec /run.sh.*|printenv GF_SERVER_ROOT_URL|' /entrypoint.sh | sh"]
    return subprocess.run(argv, capture_output=True, text=True, timeout=120).stdout.strip()


@pytest.fixture(scope="module")
def single_sign_on():
    """Switch single sign-on on for the tests that need it, and off again afterwards.

    Module scoped because each switch costs a restart of both services. Putting it back is
    what keeps this file's first half, and any test that runs later, looking at the state
    an installation actually ships in.
    """
    _set_single_sign_on(True)
    yield
    _set_single_sign_on(False)


@pytest.fixture
def browser():
    """A client that follows redirects and keeps cookies, as a browser does.

    Both matter: a sign-in is a chain of redirects between the proxy and the provider, and
    the session that comes out of it lives in a cookie.
    """
    with httpx.Client(base_url=BASE_URL, verify=VERIFY_TLS, trust_env=False,
                      follow_redirects=True, timeout=30) as client:
        yield client


class TestClientSecret:
    """The client secret is mounted, even with single sign-on switched off.

    That it exists as a podman secret at all is asserted in test_secrets.py, alongside the
    others.
    """

    @pytest.mark.parametrize("container", SSO_CONTAINERS)
    def test_the_secret_shall_be_mounted(self, container):
        # Both services read it from this path: Grafana through a $__file{} reference in
        # its configuration, crudman by reading the file in settings.py.
        podman("exec", container, "test", "-r", f"/run/secrets/{SECRETS['oidc_client']}")


class TestGrafanaConfiguration:
    """Grafana learns the host name and the provider settings from runtime.env."""

    def test_grafana_shall_receive_the_server_name(self):
        # Without the EnvironmentFile the $__env{SERVER_NAME} in custom.ini resolves to
        # nothing and Grafana falls back to localhost, which would send the sign-in
        # callback to the wrong host on any real installation.
        assert podman("exec", "grafana", "printenv", "SERVER_NAME").strip() == SERVER_NAME

    def test_grafana_shall_receive_the_issuer(self):
        assert podman("exec", "grafana", "printenv", "OIDC_ISSUER").strip() == OIDC_ISSUER


class TestCallbackAddress:
    """The address Grafana asks the provider to send the visitor back to.

    Grafana builds it from root_url rather than from the request, and root_url's own
    %(protocol)s is the protocol Grafana serves inside the pod -- http, because the proxy
    terminates TLS. Left at that, a production installation asks for an http callback its
    provider never registered, and refuses the sign-in.
    """

    def test_production_shall_use_https(self):
        derived = _derived_root_url(DEBUG="false", SERVER_NAME="reports.example.com",
                                    GRAFANA_PATH=GRAFANA_PATH)

        assert derived == f"https://reports.example.com/{GRAFANA_PATH}/"

    def test_development_shall_use_http(self):
        # The one case where the proxy serves plain HTTP, so the callback has to name it.
        derived = _derived_root_url(DEBUG="true", SERVER_NAME="localhost",
                                    GRAFANA_PATH=GRAFANA_PATH)

        assert derived == f"http://localhost/{GRAFANA_PATH}/"

    def test_an_address_set_by_hand_shall_win(self):
        # The line the README asks a custom-port installation to add, and the one this test
        # stack runs with, since it publishes ports of its own.
        explicit = f"https://reports.example.com:8443/{GRAFANA_PATH}/"

        derived = _derived_root_url(DEBUG="false", SERVER_NAME="reports.example.com",
                                    GRAFANA_PATH=GRAFANA_PATH,
                                    GF_SERVER_ROOT_URL=explicit)

        assert derived == explicit


class TestSwitchedOff:
    """With OIDC_ENABLED false, both services show their own login and nothing else."""

    def test_grafana_shall_serve_its_own_login_page(self, http):
        # auto_login follows OIDC_ENABLED, so with it off Grafana must answer here itself
        # rather than bouncing the visitor to an identity provider.
        resp = http.get(f"/{GRAFANA_PATH}/login")

        assert resp.status_code == 200

    def test_the_admin_panel_shall_serve_its_own_login_form(self, http):
        resp = http.get(CRUDMAN_LOGIN)

        assert resp.status_code == 200
        assert "csrfmiddlewaretoken" in resp.text

    def test_the_provider_routes_shall_be_absent(self, http):
        # crudman mounts allauth's URLs only when single sign-on is on. With it off the
        # address falls through to the admin's catch-all, which sends an anonymous visitor
        # to the local login page -- so what tells the two apart is where the redirect
        # leads, not whether there is one. Were allauth live, this would leave for the
        # identity provider instead.
        resp = http.get(f"/{CRUDMAN_PATH}/accounts/oidc/sso/login/")

        assert resp.status_code == 302
        assert resp.headers["location"].startswith(CRUDMAN_LOGIN)


class TestProvider:
    """The stand-in provider itself, so a later failure can be told from its own."""

    def test_the_provider_shall_publish_a_discovery_document(self):
        resp = httpx.get(f"{OIDC_ISSUER}/.well-known/openid-configuration",
                         trust_env=False, timeout=10)

        assert resp.status_code == 200
        # The address the services were configured with has to be the one it calls itself,
        # or every sign-in fails on an issuer mismatch nothing else explains.
        assert resp.json()["issuer"] == OIDC_ISSUER


class TestSigningIn:
    """A sign-in through the proxy, end to end, against the stand-in provider."""

    def test_the_admin_panel_shall_sign_a_visitor_in(self, single_sign_on, browser):
        # No login form anywhere in this: the visitor is redirected to the provider, which
        # answers with a code, and lands back inside the panel already signed in.
        resp = browser.get(f"/{CRUDMAN_PATH}/")

        assert resp.status_code == 200
        assert str(resp.url).endswith(f"/{CRUDMAN_PATH}/")

    def test_the_account_shall_be_created_with_the_role_it_was_granted(
        self, single_sign_on, browser
    ):
        browser.get(f"/{CRUDMAN_PATH}/")

        state = _django(
            "from django.contrib.auth.models import User;"
            f"u=User.objects.get(username='{SSO_USER}');"
            "print(u.is_active, u.is_staff, u.is_superuser,"
            "      sorted(g.name for g in u.groups.all()))"
        )

        # Editor opens the admin but grants no superuser, and puts the account in exactly
        # the one managed group.
        assert state == f"True True False ['{SSO_ROLE_GROUP}']"

    def test_a_group_granted_by_hand_shall_survive_a_sign_in(self, single_sign_on, browser):
        # The whole point of the scheme: a baseline from the provider, extras assigned
        # locally on top. A login that rewrote the group list instead of reconciling the
        # managed ones would drop this membership.
        browser.get(f"/{CRUDMAN_PATH}/")
        _django(
            "from django.contrib.auth.models import Group, User;"
            "g,_=Group.objects.get_or_create(name='project-b-analysts');"
            f"User.objects.get(username='{SSO_USER}').groups.add(g)"
        )

        with httpx.Client(base_url=BASE_URL, verify=VERIFY_TLS, trust_env=False,
                          follow_redirects=True, timeout=30) as second_visit:
            second_visit.get(f"/{CRUDMAN_PATH}/")

        groups = _django(
            "from django.contrib.auth.models import User;"
            f"print(sorted(g.name for g in User.objects.get(username='{SSO_USER}').groups.all()))"
        )
        assert groups == f"['project-b-analysts', '{SSO_ROLE_GROUP}']"

    def test_grafana_shall_sign_a_visitor_in(self, single_sign_on, browser):
        browser.get(f"/{GRAFANA_PATH}/")

        who = browser.get(f"/{GRAFANA_PATH}/api/user")

        assert who.status_code == 200
        assert who.json()["login"] == SSO_USER

    def test_grafana_shall_ask_to_be_called_back_where_the_browser_is(self, single_sign_on):
        # A provider accepts only the callback registered for the client, which the README
        # gives as the address Grafana is reached at -- so this one has to match it in
        # scheme, host and port alike.
        with httpx.Client(base_url=BASE_URL, verify=VERIFY_TLS, trust_env=False,
                          follow_redirects=False, timeout=10) as client:
            resp = client.get(f"/{GRAFANA_PATH}/login/generic_oauth")

        assert resp.status_code == 302
        query = parse_qs(urlparse(resp.headers["location"]).query)
        assert query["redirect_uri"] == [f"{BASE_URL}/{GRAFANA_PATH}/login/generic_oauth"]

    def test_grafana_shall_map_the_claim_to_its_own_role(self, single_sign_on, browser):
        # The mapping expression in custom.ini turning the provider's role name into one of
        # Grafana's three. Nothing else in the suite covers that expression.
        browser.get(f"/{GRAFANA_PATH}/")

        orgs = browser.get(f"/{GRAFANA_PATH}/api/user/orgs")

        assert [org["role"] for org in orgs.json()] == ["Editor"]

    def test_signing_out_of_the_admin_panel_shall_reach_the_provider(
        self, single_sign_on, browser
    ):
        # Ending only the local session would achieve nothing visible: the next page view
        # is redirected to the provider, which still holds its own session and hands the
        # person straight back. So the browser has to be sent on to end that one too.
        browser.get(f"/{CRUDMAN_PATH}/")
        csrf = browser.cookies.get("csrftoken")

        resp = browser.post(
            f"/{CRUDMAN_PATH}/logout/",
            data={"csrfmiddlewaretoken": csrf},
            headers={"Referer": f"{BASE_URL}/{CRUDMAN_PATH}/"},
            follow_redirects=False,
        )

        assert resp.status_code == 302
        assert resp.headers["location"] == f"{OIDC_ISSUER}/endsession"

    def test_signing_out_of_the_admin_panel_shall_end_the_local_session(
        self, single_sign_on, browser
    ):
        browser.get(f"/{CRUDMAN_PATH}/")
        csrf = browser.cookies.get("csrftoken")

        browser.post(
            f"/{CRUDMAN_PATH}/logout/",
            data={"csrfmiddlewaretoken": csrf},
            headers={"Referer": f"{BASE_URL}/{CRUDMAN_PATH}/"},
            follow_redirects=False,
        )

        # Signed out here whether or not the provider ever answers: asking for a page again
        # has to start a fresh sign-in rather than reuse the session that was just ended.
        with httpx.Client(base_url=BASE_URL, verify=VERIFY_TLS, trust_env=False,
                          follow_redirects=False, timeout=10) as after:
            after.cookies.update(browser.cookies)
            resp = after.get(f"/{CRUDMAN_PATH}/")

        assert resp.status_code == 302
        assert resp.headers["location"].startswith(CRUDMAN_LOGIN)

    def test_signing_out_of_grafana_shall_reach_the_provider(self, single_sign_on, browser):
        # A GET: Grafana serves its sign-out that way and answers a POST with 404.
        browser.get(f"/{GRAFANA_PATH}/")

        resp = browser.get(f"/{GRAFANA_PATH}/logout", follow_redirects=False)

        # Grafana sends a session it opened through the provider back there to be ended;
        # one from its own login form goes to its login page instead.
        assert resp.status_code == 302
        assert resp.headers["location"] == f"{OIDC_ISSUER}/endsession"

    def test_the_local_form_shall_stay_reachable(self, single_sign_on):
        # The way back in for the superuser when the provider is unreachable. Without it,
        # switching single sign-on on and misconfiguring it locks everyone out for good.
        with httpx.Client(base_url=BASE_URL, verify=VERIFY_TLS, trust_env=False,
                          follow_redirects=False, timeout=10) as client:
            crudman = client.get(f"{CRUDMAN_LOGIN}?local")
            grafana = client.get(f"/{GRAFANA_PATH}/login?disableAutoLogin")

        assert crudman.status_code == 200
        assert "csrfmiddlewaretoken" in crudman.text
        assert grafana.status_code == 200
