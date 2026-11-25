"""Grafana signs its visitors in on the admin panel's say-so.

Grafana has no login of its own. The proxy asks the admin panel about the visitor's session
cookie before every Grafana request (nginx auth_request against notebooks/grafana) and
passes the answer on in X-WEBAUTH-* headers, which Grafana's auth proxy trusts from the
loopback. So one sign-in serves the admin panel, the notebooks and Grafana alike, single
sign-on is the admin panel's alone to configure, and a person's rank decides what they may
do in all three.

Trusting a header is only safe while the header cannot be forged. The whole pod shares the
loopback, so the one naming the visitor has a name only the proxy and Grafana know.
"""

import subprocess

import pytest

from conftest import (
    BASE_URL,
    CRUDMAN_LOGIN,
    CRUDMAN_PATH,
    GRAFANA_PATH,
    SUPERUSER_NAME,
    SUPERUSER_PASSWORD,
    podman,
    sign_in,
)


@pytest.fixture(scope="module")
def signed_in():
    """One signed-in browser for the module.

    Not conftest's session-wide admin_session: that one is shared with modules that
    restart the admin panel, and a session created here and used there would be the
    first to notice. A module of its own keeps the two apart.
    """
    client = sign_in()
    yield client
    client.close()


@pytest.fixture
def own_browser():
    """A signed-in browser for one test to sign out of."""
    client = sign_in()
    yield client
    client.close()


class TestAnonymous:
    def test_a_grafana_page_shall_send_a_visitor_to_the_admin_login(self, http):
        """The login route issues Grafana's session cookie and ignores a redirect target.

        Losing a deep link at sign-in costs less than a frontend reloading forever.
        """
        resp = http.get(f"/{GRAFANA_PATH}/dashboards")

        assert resp.status_code == 302
        assert resp.headers["location"].startswith(CRUDMAN_LOGIN)
        assert f"next=/{GRAFANA_PATH}/login" in resp.headers["location"]

    def test_grafana_shall_show_no_login_form(self, http):
        """There is no password Grafana could check, so no form to take one."""
        resp = http.get(f"/{GRAFANA_PATH}/login")

        assert resp.status_code == 302

    def test_the_api_shall_refuse_too(self, http):
        """A dashboard's data, not only its page."""
        resp = http.get(f"/{GRAFANA_PATH}/api/search")

        assert resp.status_code in (302, 401)


class TestSignedIn:
    def test_the_login_route_shall_issue_grafanas_session_cookie(self, signed_in):
        """Grafana's frontend reloads endlessly without a session token.

        Its login route is the one place it issues the token on seeing the proxy's headers.
        """
        resp = signed_in.get(f"/{GRAFANA_PATH}/login", follow_redirects=False)

        assert resp.status_code == 302
        assert "grafana_session" in resp.headers.get_list("set-cookie")[0]

    def test_a_stale_token_shall_be_replaced_rather_than_refused(self, own_browser):
        """An expired or orphaned token is rotated through the login route.

        Grafana serves pages on the proxy's headers but refuses to rotate such a token,
        and its frontend would reload on the refusal forever.
        """
        stale = "0123456789abcdef0123456789abcdef"
        # Sent by hand, as a browser would keep sending them: the jar would drop the
        # stale pair the moment the login route answers with the new one.
        cookies = (
            f"sessionid={own_browser.cookies['sessionid']}; "
            f"grafana_session={stale}; grafana_session_expiry=1"
        )

        resp = own_browser.post(
            f"/{GRAFANA_PATH}/api/user/auth-tokens/rotate", headers={"Cookie": cookies}
        )

        assert resp.status_code == 200
        issued = [
            cookie
            for hop in resp.history
            for cookie in hop.headers.get_list("set-cookie")
            if cookie.startswith("grafana_session=")
        ]
        assert issued and stale not in issued[0], [str(hop.url) for hop in resp.history]

    def test_the_frontends_token_rotation_shall_succeed(self, signed_in):
        """The call whose 401 is the reload loop."""
        signed_in.get(f"/{GRAFANA_PATH}/login")

        resp = signed_in.post(f"/{GRAFANA_PATH}/api/user/auth-tokens/rotate")

        assert resp.status_code == 200

    def test_a_stale_grafana_cookie_shall_not_outlive_the_admin_panels_session(self, own_browser):
        """Grafana's cookie outlives the admin panel's sign-out; the proxy refuses it."""
        own_browser.get(f"/{GRAFANA_PATH}/login")
        assert own_browser.cookies.get("grafana_session")
        own_browser.post(f"/{CRUDMAN_PATH}/logout/",
                       data={"csrfmiddlewaretoken": own_browser.cookies["csrftoken"]},
                       headers={"Referer": f"{BASE_URL}/{CRUDMAN_PATH}/"})

        resp = own_browser.get(f"/{GRAFANA_PATH}/api/user", follow_redirects=False)

        assert resp.status_code == 302
        assert resp.headers["location"].startswith(CRUDMAN_LOGIN)

    def test_an_authorization_header_shall_not_revive_it(self, own_browser):
        """An API client's request skips the admin panel's check, so it brings no session."""
        own_browser.get(f"/{GRAFANA_PATH}/login")
        own_browser.post(f"/{CRUDMAN_PATH}/logout/",
                         data={"csrfmiddlewaretoken": own_browser.cookies["csrftoken"]},
                         headers={"Referer": f"{BASE_URL}/{CRUDMAN_PATH}/"})
        assert own_browser.cookies.get("grafana_session")

        resp = own_browser.get(f"/{GRAFANA_PATH}/api/user", follow_redirects=False,
                               headers={"Authorization": "Anything at all"})

        assert resp.status_code == 401

    def test_the_admin_panels_session_shall_open_grafana(self, signed_in):
        """The same cookie, never a second sign-in."""
        resp = signed_in.get(f"/{GRAFANA_PATH}/api/user")

        assert resp.status_code == 200
        assert resp.json()["login"] == SUPERUSER_NAME

    def test_the_rank_shall_become_the_organisation_role(self, signed_in):
        """The admin panel decides the role; Grafana is told rather than asked."""
        resp = signed_in.get(f"/{GRAFANA_PATH}/api/user/orgs")

        assert resp.status_code == 200
        assert resp.json()[0]["role"] == "Admin"

    def test_static_assets_need_no_session(self, http):
        """Exempted from the check: one subrequest per page, not one per script."""
        resp = http.get(f"/{GRAFANA_PATH}/public/img/grafana_icon.svg")

        assert resp.status_code == 200


class TestPasswords:
    """The admin panel's login is the one place to guess a password, and it is limited."""

    def test_grafana_shall_take_none_in_a_header(self, http):
        resp = http.get(f"/{GRAFANA_PATH}/api/user",
                        auth=(SUPERUSER_NAME, SUPERUSER_PASSWORD))

        assert resp.status_code == 401

    def test_grafana_shall_take_none_in_a_form(self, http):
        """With a header that skips the admin panel's check, or it would not get that far."""
        resp = http.post(f"/{GRAFANA_PATH}/login", headers={"Authorization": "Anything"},
                         json={"user": SUPERUSER_NAME, "password": SUPERUSER_PASSWORD})

        assert resp.status_code >= 400
        assert "grafana_session" not in resp.cookies


class TestForgery:
    """The header Grafana trusts is the proxy's to set, never a browser's."""

    def test_a_forged_header_shall_not_sign_anyone_in(self, http):
        """The proxy overwrites it with what the admin panel said -- nobody, here."""
        resp = http.get(f"/{GRAFANA_PATH}/api/user",
                        headers={"X-WEBAUTH-USER": SUPERUSER_NAME})

        assert resp.status_code == 302
        assert resp.headers["location"].startswith(CRUDMAN_LOGIN)

    @pytest.mark.parametrize("container", ["sqlmesh", "jupyter"])
    def test_a_container_of_the_pod_shall_not_sign_itself_in(self, container):
        """Models and notebooks run other people's code beside Grafana."""
        probe = (
            "import urllib.request as r; r.build_opener(r.ProxyHandler({})).open(r.Request("
            f"'http://127.0.0.1:3000/{GRAFANA_PATH}/api/user', "
            f"headers={{'X-WEBAUTH-USER': '{SUPERUSER_NAME}'}}))"
        )
        with pytest.raises(subprocess.CalledProcessError) as refusal:
            podman("exec", container, "python3", "-c", probe)
        assert "HTTP Error 401" in refusal.value.stderr


class TestEndpoint:
    """The admin panel's half of the contract, as the proxy calls it."""

    def test_it_shall_not_be_reachable_through_the_proxy(self, signed_in):
        """Only the proxy's subrequest comes without X-Forwarded-For."""
        resp = signed_in.get(f"/{CRUDMAN_PATH}/notebooks/grafana/")

        assert resp.status_code == 404

