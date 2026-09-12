"""Single sign-on, in both the state it ships in and the state it is meant to reach.

Two halves. The first checks the plumbing is inert while OIDC_ENABLED is false, the state
every existing installation stays in after an upgrade. The second turns it on against the
stand-in provider run-tests.sh runs inside the pod, signs in through the proxy as a browser
would, and asserts the admin panel ends up with the person the provider described -- and
Grafana with them too, though Grafana never talks to the provider: the proxy signs its
visitors in on the admin panel's say-so, so single sign-on reaches it by reaching the
admin panel (tests/test_grafana_auth.py).

The stand-in is a real OpenID Connect server, so the exchange under test is the real one.
What it does not reproduce is a directory's own behaviour -- consent screens, conditional
access, an expired client secret -- so a first sign-in against a real provider still has to
be tried by hand.
"""
import os
import subprocess
import time

import httpx
import pytest

from conftest import (
    APP_CONFIG_DIR, BASE_URL, CRUDMAN_LOGIN, CRUDMAN_PATH, GRAFANA_PATH, OIDC_ISSUER,
    RESTART_TIMEOUT, SECRETS, ROLE_PREFIX, VERIFY_TLS, inspect_container, podman,
)

# Grafana has to be told the host name: its default is "localhost", out of which it
# builds every absolute address it hands out.
SERVER_NAME = os.environ.get("SERVER_NAME", "localhost")

# The person the stand-in provider describes. Editor is the middle rank, so both a
# granted and a withheld permission can be asserted.
SSO_USER = "kim"
SSO_ROLE_GROUP = f"{ROLE_PREFIX}editor"


def _django(script):
    """Run a snippet inside the crudman container and return what it printed.

    Through uv, the application's dependencies living in its project environment rather
    than in the first interpreter on PATH. Quietly, because the shell announces the models
    it imported before running anything.
    """
    return podman(
        "exec", "crudman",
        "uv", "run", "--project", "/crudman",
        "python", "manage.py", "shell", "-v", "0", "-c", script,
    ).strip()


def _set_single_sign_on(enabled):
    """Rewrite OIDC_ENABLED in the runtime.env the services read, and restart them.

    The same edit an operator makes, followed by the same restart: a container reads its
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

    # The admin panel alone: it is the only service with a provider client, Grafana and
    # the hub taking the person it signed in from it.
    subprocess.run(["systemctl", "--user", "restart", "crudman.service"], check=True)

    deadline = time.time() + RESTART_TIMEOUT
    with httpx.Client(base_url=BASE_URL, verify=VERIFY_TLS, trust_env=False,
                      follow_redirects=False, timeout=10) as client:
        while time.time() < deadline:
            try:
                if client.get(CRUDMAN_LOGIN).status_code < 500:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(2)
    raise AssertionError("the admin panel did not come back after the restart")


def _derived_root_url(**env):
    """The public address the grafana image's entrypoint settles on for this environment.

    Runs the real entrypoint in a throwaway container with its last line -- the handover
    to Grafana's /run.sh -- replaced by a print. Reading the address off a started Grafana
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

    Module scoped, each switch costing a restart of both services. Putting it back keeps
    this file's first half, and every later test, looking at the state that ships.
    """
    _set_single_sign_on(True)
    yield
    _set_single_sign_on(False)


@pytest.fixture
def browser():
    """A client that follows redirects and keeps cookies, as a browser does.

    A sign-in is a chain of redirects between the proxy and the provider, and the session
    that comes out of it lives in a cookie.
    """
    with httpx.Client(base_url=BASE_URL, verify=VERIFY_TLS, trust_env=False,
                      follow_redirects=True, timeout=30) as client:
        yield client


class TestClientSecret:
    """The client secret is mounted where the provider client lives, even with single
    sign-on switched off. That it exists as a podman secret is asserted in test_secrets.py.
    """

    def test_the_secret_shall_be_mounted_in_the_admin_panel(self):
        podman("exec", "crudman", "test", "-r", f"/run/secrets/{SECRETS['oidc_client']}")

    def test_grafana_shall_not_have_it(self):
        """Grafana is no provider client: it takes the visitor from the proxy. A secret it
        cannot use is one more place it could leak from."""
        result = subprocess.run(
            ["podman", "exec", "grafana", "test", "-e", f"/run/secrets/{SECRETS['oidc_client']}"],
            capture_output=True,
        )
        assert result.returncode != 0


class TestGrafanaConfiguration:
    """Grafana learns the host name from runtime.env, and nothing about the provider."""

    def test_grafana_shall_receive_the_server_name(self):
        # Without the EnvironmentFile, $__env{SERVER_NAME} in custom.ini resolves to
        # nothing and Grafana falls back to localhost.
        assert podman("exec", "grafana", "printenv", "SERVER_NAME").strip() == SERVER_NAME

    def test_grafana_shall_have_no_provider_client(self):
        """The whole generic_oauth block is gone, not merely switched off: with the proxy
        signing visitors in, a second client would be a second identity for the same
        person, and a second thing to register with the provider."""
        config = podman("exec", "grafana", "cat", "/etc/grafana/grafana.ini")
        assert "[auth.generic_oauth]" not in config
        assert "[auth.proxy]" in config


class TestRootAddress:
    """The address Grafana puts into every absolute link it builds.

    Grafana takes it from root_url rather than from the request, and root_url's
    %(protocol)s is what Grafana serves inside the pod -- http, the proxy terminating TLS.
    Left at that, production hands out links naming the wrong scheme; the assistant
    dashboard's MCP address is one of them.
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
        # The line the README asks a custom-port installation to add, as this stack is.
        explicit = f"https://reports.example.com:8443/{GRAFANA_PATH}/"

        derived = _derived_root_url(DEBUG="false", SERVER_NAME="reports.example.com",
                                    GRAFANA_PATH=GRAFANA_PATH,
                                    GF_SERVER_ROOT_URL=explicit)

        assert derived == explicit


class TestSwitchedOff:
    """With OIDC_ENABLED false, the admin panel shows its own login and nothing else."""

    def test_the_admin_panel_shall_serve_its_own_login_form(self, http):
        resp = http.get(CRUDMAN_LOGIN)

        assert resp.status_code == 200
        assert "csrfmiddlewaretoken" in resp.text

    def test_the_provider_routes_shall_be_absent(self, http):
        # With single sign-on off the address falls through to the admin's catch-all,
        # which sends an anonymous visitor to the local login page. What tells the two
        # apart is where the redirect leads, not whether there is one.
        resp = http.get(f"/{CRUDMAN_PATH}/accounts/oidc/sso/login/")

        assert resp.status_code == 302
        assert resp.headers["location"].startswith(CRUDMAN_LOGIN)


class TestProvider:
    """The stand-in provider itself, so a later failure can be told from its own."""

    def test_the_provider_shall_publish_a_discovery_document(self):
        resp = httpx.get(f"{OIDC_ISSUER}/.well-known/openid-configuration",
                         trust_env=False, timeout=10)

        assert resp.status_code == 200
        # A mismatch between this and the configured address fails every sign-in on an
        # issuer error nothing else explains.
        assert resp.json()["issuer"] == OIDC_ISSUER


class TestSigningIn:
    """A sign-in through the proxy, end to end, against the stand-in provider."""

    def test_the_admin_panel_shall_sign_a_visitor_in(self, single_sign_on, browser):
        # No login form: the visitor goes to the provider, which answers with a code, and
        # lands back inside the panel signed in.
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

        # Editor opens the admin, grants no superuser, and joins one managed group.
        assert state == f"True True False ['{SSO_ROLE_GROUP}']"

    def test_a_group_granted_by_hand_shall_survive_a_sign_in(self, single_sign_on, browser):
        # The point of the scheme: a baseline from the provider, extras assigned locally
        # on top.
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
        # Sorted, so the expectation follows ROLE_PREFIX rather than an assumed order.
        assert groups == str(sorted(["project-b-analysts", SSO_ROLE_GROUP]))

    def test_grafana_shall_know_the_visitor_the_provider_described(self, single_sign_on, browser):
        # Grafana never met the provider. Opening it with the session the admin panel
        # got from the provider is the whole sign-in: the proxy asks the admin panel,
        # and Grafana takes its word.
        browser.get(f"/{GRAFANA_PATH}/")

        who = browser.get(f"/{GRAFANA_PATH}/api/user")

        assert who.status_code == 200
        assert who.json()["login"] == SSO_USER

    def test_grafana_shall_hold_the_role_the_provider_granted(self, single_sign_on, browser):
        # The provider's claim became a group in the admin panel; the admin panel's
        # answer to the proxy turns that group into one of Grafana's three roles. One
        # mapping, in notebooks/views.py, rather than one per service.
        browser.get(f"/{GRAFANA_PATH}/")

        orgs = browser.get(f"/{GRAFANA_PATH}/api/user/orgs")

        assert [org["role"] for org in orgs.json()] == ["Editor"]

    def test_signing_out_of_the_admin_panel_shall_reach_the_provider(
        self, single_sign_on, browser
    ):
        # Ending only the local session achieves nothing: the provider still holds its
        # own and hands the person straight back at the next page view.
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

        # Signed out here whether or not the provider answers, so the next page view
        # starts a fresh sign-in.
        with httpx.Client(base_url=BASE_URL, verify=VERIFY_TLS, trust_env=False,
                          follow_redirects=False, timeout=10) as after:
            after.cookies.update(browser.cookies)
            resp = after.get(f"/{CRUDMAN_PATH}/")

        assert resp.status_code == 302
        assert resp.headers["location"].startswith(CRUDMAN_LOGIN)

    def test_signing_out_of_grafana_shall_lead_to_the_admin_panel(self, single_sign_on, browser):
        # Signing out of Grafana alone would be undone by the next request, which brings
        # the proxy's headers back. The session that counts is the admin panel's, whose
        # sign-out is a button, not an address a link could trigger -- so Grafana's
        # sign-out leads to the admin panel, where that button ends the session and,
        # with single sign-on on, the provider's. Grafana serves its sign-out on a GET.
        browser.get(f"/{GRAFANA_PATH}/")

        resp = browser.get(f"/{GRAFANA_PATH}/logout", follow_redirects=False)

        assert resp.status_code == 302
        assert resp.headers["location"].rstrip("/") == f"/{CRUDMAN_PATH}"

    def test_the_local_form_shall_stay_reachable(self, single_sign_on):
        # The way back in for the superuser when the provider is unreachable -- and, the
        # admin panel's sign-in being everyone's, the way back into Grafana too.
        with httpx.Client(base_url=BASE_URL, verify=VERIFY_TLS, trust_env=False,
                          follow_redirects=False, timeout=10) as client:
            crudman = client.get(f"{CRUDMAN_LOGIN}?local")

        assert crudman.status_code == 200
        assert "csrfmiddlewaretoken" in crudman.text
